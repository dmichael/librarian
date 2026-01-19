"""Query the Librarian vector store with dual collection support.

Queries both:
1. librarian_full - text chunks from documents
2. librarian_equations - extracted mathematical equations

Results are merged and ranked by relevance score.
"""

import sys
from dataclasses import dataclass

from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.vector_stores import MetadataFilters, MetadataFilter, FilterOperator
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from librarian.config import load_config
from librarian.vectorstore import get_vector_store, get_collection_names
from librarian.vectorstore.protocol import LibrarianVectorStore


def setup_chapter_retriever(store, collection_name: str, top_k: int = 3):
    """Set up retriever for the chapter collection.

    Args:
        store: LibrarianVectorStore instance
        collection_name: Name of the chapter collection
        top_k: Number of chapters to retrieve
    """
    if not store.collection_exists(collection_name):
        return None

    vector_store = store.get_llama_store(collection_name)
    index = VectorStoreIndex.from_vector_store(vector_store)

    return index.as_retriever(similarity_top_k=top_k)


@dataclass
class RetrievalResult:
    """Unified result from either text or equation collection."""
    text: str
    score: float
    metadata: dict
    result_type: str  # "text" or "equation"

    @property
    def is_equation(self) -> bool:
        return self.result_type == "equation"


def setup_retriever(
    config: dict,
    top_k: int = 5,
    subjects: list[str] | None = None,
    library: str | None = None,
    store: LibrarianVectorStore | None = None,
    block_type: str | None = None,
):
    """Set up the retriever with embedding model and vector store.

    Args:
        config: Application configuration
        top_k: Number of results to retrieve
        subjects: Optional list of subjects to filter by (e.g., ["psychology/*"])
        library: Optional library to restrict search to (e.g., "therapy")
        store: Optional pre-initialized vector store (avoids creating new one)
        block_type: Optional block type to filter by (e.g., "Code", "TableOfContents")
    """
    # Embedding config
    embedding_config = config.get("embedding", {})
    model_name = embedding_config.get("model", "BAAI/bge-base-en-v1.5")
    device = embedding_config.get("device", "cpu")

    # For BGE models, add query instruction
    if "bge" in model_name.lower():
        embed_model = HuggingFaceEmbedding(
            model_name=model_name,
            device=device,
            query_instruction="Represent this sentence for searching relevant passages: ",
        )
    else:
        embed_model = HuggingFaceEmbedding(model_name=model_name, device=device)

    Settings.embed_model = embed_model

    # Get vector store
    if store is None:
        store = get_vector_store(config)

    collections = get_collection_names(config)
    vector_store = store.get_llama_store(collections["full"])

    # Create index from existing store
    index = VectorStoreIndex.from_vector_store(vector_store)

    # Build filters if subjects or library specified
    filters = None
    filter_list = []

    # Library filter (exact match, AND with subjects)
    if library:
        filter_list.append(
            MetadataFilter(key="library", value=library, operator=FilterOperator.EQ)
        )

    # Block type filter (exact match)
    if block_type:
        filter_list.append(
            MetadataFilter(key="block_type", value=block_type, operator=FilterOperator.EQ)
        )

    # Subject filters (OR among subjects)
    if subjects:
        # Qdrant supports: EQ, NE, GT, GTE, LT, LTE, IN, NIN, TEXT_MATCH, IS_EMPTY
        subject_filters = []
        for subj in subjects:
            if subj.endswith("/*"):
                # Wildcard match: "psychology/*" matches "psychology/therapy"
                prefix = subj[:-2]
                subject_filters.append(
                    MetadataFilter(key="subjects", value=prefix, operator=FilterOperator.TEXT_MATCH)
                )
            else:
                # Exact match
                subject_filters.append(
                    MetadataFilter(key="subjects", value=subj, operator=FilterOperator.TEXT_MATCH)
                )
        # If we have library filter, we need to AND it with OR'd subjects
        if library and subject_filters:
            # Library AND (subject1 OR subject2 OR ...)
            filter_list.append(MetadataFilters(filters=subject_filters, condition="or"))
            filters = MetadataFilters(filters=filter_list, condition="and")
        elif subject_filters:
            filters = MetadataFilters(filters=subject_filters, condition="or")
    elif filter_list:
        filters = MetadataFilters(filters=filter_list, condition="and")

    # Return retriever (no LLM needed)
    return index.as_retriever(similarity_top_k=top_k, filters=filters)


def setup_equation_retriever(store, collection_name: str, top_k: int = 3):
    """Set up retriever for the equation collection.

    Args:
        store: LibrarianVectorStore instance
        collection_name: Name of the equation collection
        top_k: Number of equations to retrieve
    """
    if not store.collection_exists(collection_name):
        return None

    vector_store = store.get_llama_store(collection_name)
    index = VectorStoreIndex.from_vector_store(vector_store)

    return index.as_retriever(similarity_top_k=top_k)


def retrieve(
    query_text: str,
    config: dict | None = None,
    top_k: int = 5,
    subjects: list[str] | None = None,
    library: str | None = None,
    include_equations: bool = True,
    equation_top_k: int = 3,
    block_type: str | None = None,
):
    """Run a retrieval query across text and equation collections.

    Args:
        query_text: The search query
        config: Optional config (loads default if not provided)
        top_k: Number of text results to retrieve
        subjects: Optional subject filters (e.g., ["psychology/*", "self-help/*"])
        library: Optional library to restrict search to (e.g., "therapy")
        include_equations: Whether to also search equation collection
        equation_top_k: Number of equations to retrieve
        block_type: Optional block type filter (e.g., "Code", "TableOfContents")

    Returns:
        List of nodes (text chunks and/or equations) sorted by score
    """
    if config is None:
        config = load_config()

    # Get vector store and collection names
    store = get_vector_store(config)
    collections = get_collection_names(config)

    # Setup embedding model (shared)
    embedding_config = config.get("embedding", {})
    model_name = embedding_config.get("model", "BAAI/bge-base-en-v1.5")
    device = embedding_config.get("device", "cpu")

    if "bge" in model_name.lower():
        embed_model = HuggingFaceEmbedding(
            model_name=model_name,
            device=device,
            query_instruction="Represent this sentence for searching relevant passages: ",
        )
    else:
        embed_model = HuggingFaceEmbedding(model_name=model_name, device=device)

    Settings.embed_model = embed_model

    # Get text chunks
    text_store = store.get_llama_store(collections["full"])
    text_index = VectorStoreIndex.from_vector_store(text_store)

    # Build filters
    filters = _build_filters(subjects, library, block_type)
    text_retriever = text_index.as_retriever(similarity_top_k=top_k, filters=filters)
    text_nodes = text_retriever.retrieve(query_text)

    # Mark text nodes
    for node in text_nodes:
        node.metadata["_result_type"] = "text"

    if not include_equations:
        return text_nodes

    # Get equations from separate collection
    eq_retriever = setup_equation_retriever(store, collections["equations"], top_k=equation_top_k)

    if eq_retriever:
        eq_nodes = eq_retriever.retrieve(query_text)
        # Mark equation nodes (context_window available in metadata for display)
        for node in eq_nodes:
            node.metadata["_result_type"] = "equation"

        # Filter equations to only those from same books as top text results
        # This prevents cross-contamination (e.g., birdsong equations in finance queries)
        if text_nodes and eq_nodes:
            relevant_book_ids = {n.metadata.get("book_id") for n in text_nodes[:3]}
            eq_nodes = [e for e in eq_nodes if e.metadata.get("book_id") in relevant_book_ids]
    else:
        eq_nodes = []

    # Merge and sort by score
    all_nodes = text_nodes + eq_nodes
    all_nodes.sort(key=lambda n: n.score, reverse=True)

    return all_nodes


def _build_filters(subjects: list[str] | None, library: str | None, block_type: str | None = None):
    """Build metadata filters for retrieval."""
    filter_list = []

    if library:
        filter_list.append(
            MetadataFilter(key="library", value=library, operator=FilterOperator.EQ)
        )

    if block_type:
        filter_list.append(
            MetadataFilter(key="block_type", value=block_type, operator=FilterOperator.EQ)
        )

    if subjects:
        subject_filters = []
        for subj in subjects:
            if subj.endswith("/*"):
                prefix = subj[:-2]
                subject_filters.append(
                    MetadataFilter(key="subjects", value=prefix, operator=FilterOperator.TEXT_MATCH)
                )
            else:
                subject_filters.append(
                    MetadataFilter(key="subjects", value=subj, operator=FilterOperator.TEXT_MATCH)
                )
        if library and subject_filters:
            filter_list.append(MetadataFilters(filters=subject_filters, condition="or"))
            return MetadataFilters(filters=filter_list, condition="and")
        elif subject_filters:
            return MetadataFilters(filters=subject_filters, condition="or")

    if filter_list:
        return MetadataFilters(filters=filter_list, condition="and")

    return None


def retrieve_chapters(
    query_text: str,
    config: dict | None = None,
    top_k: int = 3,
    book_id: int | None = None,
) -> list:
    """Retrieve relevant chapters from the chapter collection.

    Useful for:
    - Broad queries that need chapter-level context
    - Navigation queries like "what's in chapter 5?"
    - Finding which chapters discuss a topic

    Args:
        query_text: The search query
        config: Optional config (loads default if not provided)
        top_k: Number of chapters to retrieve
        book_id: Optional book ID to restrict search to

    Returns:
        List of chapter nodes with metadata (chapter_num, summary, section_titles)
    """
    if config is None:
        config = load_config()

    # Get vector store and collection names
    store = get_vector_store(config)
    collections = get_collection_names(config)

    # Setup embedding model
    embedding_config = config.get("embedding", {})
    model_name = embedding_config.get("model", "BAAI/bge-base-en-v1.5")
    device = embedding_config.get("device", "cpu")

    if "bge" in model_name.lower():
        embed_model = HuggingFaceEmbedding(
            model_name=model_name,
            device=device,
            query_instruction="Represent this sentence for searching relevant passages: ",
        )
    else:
        embed_model = HuggingFaceEmbedding(model_name=model_name, device=device)

    Settings.embed_model = embed_model

    ch_retriever = setup_chapter_retriever(store, collections["chapters"], top_k=top_k)

    if not ch_retriever:
        return []

    # Add book_id filter if specified
    if book_id:
        filters = MetadataFilters(
            filters=[MetadataFilter(key="book_id", value=book_id, operator=FilterOperator.EQ)]
        )
        ch_retriever = ch_retriever.index.as_retriever(similarity_top_k=top_k, filters=filters)

    return ch_retriever.retrieve(query_text)


def retrieve_hierarchical(
    query_text: str,
    config: dict | None = None,
    top_k: int = 5,
    chapter_top_k: int = 3,
    subjects: list[str] | None = None,
    library: str | None = None,
    include_equations: bool = True,
) -> list:
    """Two-stage hierarchical retrieval: chapters first, then chunks within.

    For broad queries, this finds relevant chapters first, then searches
    within those chapters for specific content. This improves precision
    by narrowing the search space based on structural relevance.

    Args:
        query_text: The search query
        config: Optional config (loads default if not provided)
        top_k: Number of text results to retrieve
        chapter_top_k: Number of chapters to consider in first stage
        subjects: Optional subject filters
        library: Optional library to restrict search to
        include_equations: Whether to also search equation collection

    Returns:
        List of nodes (text chunks and/or equations) from relevant chapters
    """
    if config is None:
        config = load_config()

    # Stage 1: Find relevant chapters
    chapter_nodes = retrieve_chapters(query_text, config, top_k=chapter_top_k)

    if not chapter_nodes:
        # Fall back to standard retrieval if no chapter structure
        return retrieve(query_text, config, top_k, subjects, library, include_equations)

    # Extract chapter numbers from results
    chapter_nums = []
    for node in chapter_nodes:
        ch_num = node.metadata.get("chapter_num")
        if ch_num is not None:
            chapter_nums.append(ch_num)

    if not chapter_nums:
        return retrieve(query_text, config, top_k, subjects, library, include_equations)

    # Stage 2: Search chunks within those chapters
    store = get_vector_store(config)
    collections = get_collection_names(config)

    # Setup embedding model
    embedding_config = config.get("embedding", {})
    model_name = embedding_config.get("model", "BAAI/bge-base-en-v1.5")
    device = embedding_config.get("device", "cpu")

    if "bge" in model_name.lower():
        embed_model = HuggingFaceEmbedding(
            model_name=model_name,
            device=device,
            query_instruction="Represent this sentence for searching relevant passages: ",
        )
    else:
        embed_model = HuggingFaceEmbedding(model_name=model_name, device=device)

    Settings.embed_model = embed_model

    text_store = store.get_llama_store(collections["full"])
    text_index = VectorStoreIndex.from_vector_store(text_store)

    # Build filter for chapter numbers
    chapter_filter = MetadataFilter(
        key="chapter_num",
        value=chapter_nums,
        operator=FilterOperator.IN
    )

    # Combine with subject/library filters if provided
    filter_list = [chapter_filter]
    if library:
        filter_list.append(
            MetadataFilter(key="library", value=library, operator=FilterOperator.EQ)
        )
    if subjects:
        subject_filters = []
        for subj in subjects:
            if subj.endswith("/*"):
                prefix = subj[:-2]
                subject_filters.append(
                    MetadataFilter(key="subjects", value=prefix, operator=FilterOperator.TEXT_MATCH)
                )
            else:
                subject_filters.append(
                    MetadataFilter(key="subjects", value=subj, operator=FilterOperator.TEXT_MATCH)
                )
        if subject_filters:
            filter_list.append(MetadataFilters(filters=subject_filters, condition="or"))

    filters = MetadataFilters(filters=filter_list, condition="and")

    text_retriever = text_index.as_retriever(similarity_top_k=top_k, filters=filters)
    text_nodes = text_retriever.retrieve(query_text)

    # Mark text nodes
    for node in text_nodes:
        node.metadata["_result_type"] = "text"

    if not include_equations:
        return text_nodes

    # Get equations (already filtered by book in retrieve())
    eq_retriever = setup_equation_retriever(store, collections["equations"], top_k=3)
    if eq_retriever:
        eq_nodes = eq_retriever.retrieve(query_text)
        for node in eq_nodes:
            node.metadata["_result_type"] = "equation"

        # Filter to same books as text results
        if text_nodes and eq_nodes:
            relevant_book_ids = {n.metadata.get("book_id") for n in text_nodes[:3]}
            eq_nodes = [e for e in eq_nodes if e.metadata.get("book_id") in relevant_book_ids]
    else:
        eq_nodes = []

    all_nodes = text_nodes + eq_nodes
    all_nodes.sort(key=lambda n: n.score, reverse=True)

    return all_nodes


def retrieve_equations_only(
    query_text: str,
    config: dict | None = None,
    top_k: int = 10,
) -> list:
    """Retrieve only from the equation collection.

    Useful for queries like "list all differential equations" or
    "find equations related to oscillators".
    """
    if config is None:
        config = load_config()

    # Get vector store and collection names
    store = get_vector_store(config)
    collections = get_collection_names(config)

    # Setup embedding model
    embedding_config = config.get("embedding", {})
    model_name = embedding_config.get("model", "BAAI/bge-base-en-v1.5")
    device = embedding_config.get("device", "cpu")

    if "bge" in model_name.lower():
        embed_model = HuggingFaceEmbedding(
            model_name=model_name,
            device=device,
            query_instruction="Represent this sentence for searching relevant passages: ",
        )
    else:
        embed_model = HuggingFaceEmbedding(model_name=model_name, device=device)

    Settings.embed_model = embed_model

    eq_retriever = setup_equation_retriever(store, collections["equations"], top_k=top_k)

    if not eq_retriever:
        return []

    return eq_retriever.retrieve(query_text)


def main():
    """CLI entry point for querying."""
    # Parse args
    query_parts = []
    subjects = []
    library = None
    block_type = None
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--subject" and i + 1 < len(sys.argv):
            subjects.append(sys.argv[i + 1])
            i += 1
        elif arg == "--library" and i + 1 < len(sys.argv):
            library = sys.argv[i + 1]
            i += 1
        elif arg == "--block-type" and i + 1 < len(sys.argv):
            block_type = sys.argv[i + 1]
            i += 1
        elif arg in ("-h", "--help"):
            print("Usage: librarian-query [OPTIONS] <query>")
            print("\nOptions:")
            print("  --library TYPE    Restrict to a specific library")
            print("  --subject SUBJ    Filter by subject (can use multiple times)")
            print("  --block-type TYPE Filter by block type (Code, TableOfContents, etc.)")
            print("\nBlock types: Text, Code, SectionHeader, Equation, Table,")
            print("             TableOfContents, ListGroup, Figure, Caption")
            print("\nExamples:")
            print("  librarian-query 'wallet encryption'")
            print("  librarian-query --block-type Code 'bitcoin transaction'")
            print("  librarian-query --block-type TableOfContents 'chapters'")
            sys.exit(0)
        else:
            query_parts.append(arg)
        i += 1

    if not query_parts:
        print("Error: No query provided")
        sys.exit(1)

    query_text = " ".join(query_parts)
    config = load_config()

    print(f"Query: {query_text}")
    if library:
        print(f"Library: {library}")
    if subjects:
        print(f"Subjects: {subjects}")
    if block_type:
        print(f"Block type: {block_type}")
    print("\nLoading embedding model...")

    nodes = retrieve(
        query_text,
        config,
        subjects=subjects if subjects else None,
        library=library,
        block_type=block_type,
    )

    print("=" * 60)
    print(f"TOP {len(nodes)} RESULTS:")
    print("=" * 60)
    for i, node in enumerate(nodes, 1):
        print(f"\n[{i}] Score: {node.score:.4f}")
        print(f"    Book: {node.metadata.get('title', 'Unknown')}")
        node_library = node.metadata.get('library', '')
        if node_library:
            print(f"    Library: {node_library}")
        node_subjects = node.metadata.get('subjects', [])
        if node_subjects:
            print(f"    Subjects: {node_subjects}")
        print("-" * 40)
        # Show more text for useful context
        print(node.text[:500])
        if len(node.text) > 500:
            print("...")


if __name__ == "__main__":
    main()
