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
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from librarian.config import expand_path, load_config


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
):
    """Set up the retriever with embedding model and vector store.

    Args:
        config: Application configuration
        top_k: Number of results to retrieve
        subjects: Optional list of subjects to filter by (e.g., ["psychology/*"])
        library: Optional library to restrict search to (e.g., "therapy")
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

    # Vector store config
    vs_config = config.get("vector_store", {})
    path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))
    collection = vs_config.get("collection", "librarian_full")

    client = QdrantClient(path=str(path))
    vector_store = QdrantVectorStore(client=client, collection_name=collection)

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


def setup_equation_retriever(config: dict, client: QdrantClient, top_k: int = 3):
    """Set up retriever for the equation collection.

    Args:
        config: Application configuration
        client: Existing Qdrant client (reuse connection)
        top_k: Number of equations to retrieve
    """
    vs_config = config.get("vector_store", {})
    collection = vs_config.get("equation_collection", "librarian_equations")

    # Check if collection exists
    try:
        collections = client.get_collections().collections
        if not any(c.name == collection for c in collections):
            return None
    except Exception:
        return None

    vector_store = QdrantVectorStore(client=client, collection_name=collection)
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

    Returns:
        List of nodes (text chunks and/or equations) sorted by score
    """
    if config is None:
        config = load_config()

    # Setup shared Qdrant client
    vs_config = config.get("vector_store", {})
    path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))
    client = QdrantClient(path=str(path))

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
    collection = vs_config.get("collection", "librarian_full")
    text_store = QdrantVectorStore(client=client, collection_name=collection)
    text_index = VectorStoreIndex.from_vector_store(text_store)

    # Build filters
    filters = _build_filters(subjects, library)
    text_retriever = text_index.as_retriever(similarity_top_k=top_k, filters=filters)
    text_nodes = text_retriever.retrieve(query_text)

    # Mark text nodes
    for node in text_nodes:
        node.metadata["_result_type"] = "text"

    if not include_equations:
        return text_nodes

    # Get equations from separate collection (same client)
    eq_retriever = setup_equation_retriever(config, client, top_k=equation_top_k)

    if eq_retriever:
        eq_nodes = eq_retriever.retrieve(query_text)
        # Mark equation nodes (context_window available in metadata for display)
        for node in eq_nodes:
            node.metadata["_result_type"] = "equation"
    else:
        eq_nodes = []

    # Merge and sort by score
    all_nodes = text_nodes + eq_nodes
    all_nodes.sort(key=lambda n: n.score, reverse=True)

    return all_nodes


def _build_filters(subjects: list[str] | None, library: str | None):
    """Build metadata filters for retrieval."""
    filter_list = []

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
        if library and subject_filters:
            filter_list.append(MetadataFilters(filters=subject_filters, condition="or"))
            return MetadataFilters(filters=filter_list, condition="and")
        elif subject_filters:
            return MetadataFilters(filters=subject_filters, condition="or")

    if filter_list:
        return MetadataFilters(filters=filter_list, condition="and")

    return None


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

    vs_config = config.get("vector_store", {})
    path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))

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

    client = QdrantClient(path=str(path))
    eq_retriever = setup_equation_retriever(config, client, top_k=top_k)

    if not eq_retriever:
        return []

    return eq_retriever.retrieve(query_text)


def main():
    """CLI entry point for querying."""
    # Parse args
    query_parts = []
    subjects = []
    library = None
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--subject" and i + 1 < len(sys.argv):
            subjects.append(sys.argv[i + 1])
            i += 1
        elif arg == "--library" and i + 1 < len(sys.argv):
            library = sys.argv[i + 1]
            i += 1
        elif arg in ("-h", "--help"):
            print("Usage: librarian-query [--library NAME] [--subject SUBJECT ...] <query>")
            print("  --library   Restrict to a specific library (e.g., --library therapy)")
            print("  --subject   Filter by subject (e.g., --subject psychology/*)")
            print("              Can be used multiple times for OR matching")
            print("\nExamples:")
            print("  librarian-query --library therapy 'emotion regulation'")
            print("  librarian-query --subject psychology/* 'crisis skills'")
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
    print("\nLoading embedding model...")

    nodes = retrieve(
        query_text,
        config,
        subjects=subjects if subjects else None,
        library=library,
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
