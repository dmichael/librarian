"""Index extracted books into Qdrant vector store."""

import json
import re
import subprocess
import sys
from pathlib import Path

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from librarian.config import expand_path, load_config


def extract_page_number(text: str) -> int | None:
    """Extract page number from Marker's embedded page markers."""
    # Look for <span id="page-XXX"> or image refs like _page_XXX_
    page_spans = re.findall(r'page-(\d+)', text)
    page_images = re.findall(r'_page_(\d+)_', text)
    pages = page_spans + page_images
    if pages:
        return int(pages[0])  # Return first page found
    return None


def get_calibre_metadata(library_path: Path) -> dict[int, dict]:
    """Get book metadata from Calibre for all books."""
    cmd = [
        "calibredb", "list",
        "--library-path", str(library_path),
        "--fields", "id,title,authors,tags,publisher,pubdate,*subjects,*library",
        "--for-machine",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error querying Calibre: {result.stderr}", file=sys.stderr)
        return {}

    books = json.loads(result.stdout)

    # Parse subjects (may be string or list depending on Calibre version)
    for book in books:
        subjects_raw = book.get("*subjects", [])
        if isinstance(subjects_raw, str):
            book["subjects"] = [s.strip() for s in subjects_raw.split(",")] if subjects_raw else []
        elif isinstance(subjects_raw, list):
            book["subjects"] = subjects_raw
        else:
            book["subjects"] = []

    return {book["id"]: book for book in books}


def load_extracted_book(book_dir: Path) -> str | None:
    """Load the extracted markdown for a book."""
    full_md = book_dir / "full.md"
    if not full_md.exists():
        return None
    return full_md.read_text()


def create_documents(
    book_id: int,
    content: str,
    metadata: dict,
) -> list[Document]:
    """Create LlamaIndex documents with metadata."""
    # Build metadata for the document
    # subjects is a list for filtering (e.g., ["psychology/therapy", "self-help/skills-training"])
    subjects = metadata.get("subjects", [])
    library = metadata.get("*library", "") or ""

    doc_metadata = {
        "book_id": book_id,
        "title": metadata.get("title", "Unknown"),
        "authors": ", ".join(metadata.get("authors", [])),
        "tags": metadata.get("tags", []),
        "publisher": metadata.get("publisher", ""),
        "subjects": subjects,
        "library": library,  # Bounded collection for agent access
    }

    # Create a single document - chunking happens via node parser
    return [Document(text=content, metadata=doc_metadata)]


def setup_embedding_model(config: dict) -> HuggingFaceEmbedding:
    """Initialize the embedding model."""
    embedding_config = config.get("embedding", {})
    model_name = embedding_config.get("model", "BAAI/bge-base-en-v1.5")
    device = embedding_config.get("device", "cpu")

    print(f"Loading embedding model: {model_name} on {device}")

    # For BGE models, we need to add the query instruction
    if "bge" in model_name.lower():
        return HuggingFaceEmbedding(
            model_name=model_name,
            device=device,
            query_instruction="Represent this sentence for searching relevant passages: ",
        )
    return HuggingFaceEmbedding(model_name=model_name, device=device)


def setup_vector_store(config: dict) -> tuple[QdrantClient, QdrantVectorStore]:
    """Initialize Qdrant client and vector store."""
    vs_config = config.get("vector_store", {})
    path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))
    collection = vs_config.get("collection", "librarian_full")

    # Ensure directory exists
    path.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to Qdrant at: {path}")

    # Create client with local persistence
    client = QdrantClient(path=str(path))

    # Create vector store
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection,
    )

    return client, vector_store


def index_book(
    book_id: int,
    content: str,
    metadata: dict,
    vector_store: QdrantVectorStore,
    config: dict,
) -> int:
    """Index a single book and return number of chunks created."""
    # Create documents
    documents = create_documents(book_id, content, metadata)

    # Set up chunking
    chunk_config = config.get("chunking", {})
    chunk_size = chunk_config.get("chunk_size", 512)
    chunk_overlap = chunk_config.get("chunk_overlap", 50)

    node_parser = SentenceSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    # Parse nodes first so we can add page numbers
    nodes = node_parser.get_nodes_from_documents(documents)

    # Extract page numbers and add to node metadata
    last_known_page = None
    for node in nodes:
        page = extract_page_number(node.text)
        if page:
            last_known_page = page
        # Use last known page if current chunk doesn't have one
        node.metadata["page"] = last_known_page

    # Create storage context with vector store
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    # Build index from nodes (this embeds and stores)
    index = VectorStoreIndex(
        nodes=nodes,
        storage_context=storage_context,
        show_progress=True,
    )

    return len(nodes)


def get_indexed_books(client: QdrantClient, collection: str) -> set[int]:
    """Get set of book_ids already indexed."""
    try:
        # Check if collection exists
        collections = client.get_collections().collections
        if not any(c.name == collection for c in collections):
            return set()

        # Scroll through all points to get unique book_ids
        indexed = set()
        offset = None
        while True:
            results, offset = client.scroll(
                collection_name=collection,
                limit=1000,
                offset=offset,
                with_payload=["book_id"],
            )
            if not results:
                break
            for point in results:
                if point.payload and "book_id" in point.payload:
                    indexed.add(point.payload["book_id"])
            if offset is None:
                break

        return indexed
    except Exception:
        return set()


def parse_args():
    """Parse command line arguments."""
    args = {
        "force": False,
        "book_ids": [],
    }

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--force":
            args["force"] = True
        elif arg == "--book-id" and i + 1 < len(sys.argv):
            args["book_ids"].append(int(sys.argv[i + 1]))
            i += 1
        elif arg in ("-h", "--help"):
            print("Usage: librarian-index [--force] [--book-id ID ...]")
            print("  --force     Re-index books even if already indexed")
            print("  --book-id   Only index specific book IDs")
            sys.exit(0)
        i += 1

    return args


def main():
    """CLI entry point for indexing."""
    args = parse_args()

    config = load_config()
    library_path = expand_path(config["library_path"])
    output_path = expand_path(config["output_path"])

    # Get Calibre metadata for all books
    calibre_metadata = get_calibre_metadata(library_path)
    if not calibre_metadata:
        print("No books found in Calibre library")
        return

    # Set up embedding model (global settings for LlamaIndex)
    embed_model = setup_embedding_model(config)
    Settings.embed_model = embed_model

    # Set up vector store
    client, vector_store = setup_vector_store(config)

    # Get already indexed books
    collection = config.get("vector_store", {}).get("collection", "librarian_full")
    indexed_books = set() if args["force"] else get_indexed_books(client, collection)

    # Find extracted books
    extracted_dirs = [d for d in output_path.iterdir() if d.is_dir() and d.name.isdigit()]

    total_chunks = 0
    for book_dir in sorted(extracted_dirs, key=lambda d: int(d.name)):
        book_id = int(book_dir.name)

        # Filter by book ID if specified
        if args["book_ids"] and book_id not in args["book_ids"]:
            continue

        # Skip if already indexed
        if book_id in indexed_books:
            title = calibre_metadata.get(book_id, {}).get("title", "Unknown")
            print(f"[{book_id}] {title}: Already indexed, skipping")
            continue

        # Load content
        content = load_extracted_book(book_dir)
        if not content:
            print(f"[{book_id}] No extracted content found, skipping")
            continue

        # Get metadata
        metadata = calibre_metadata.get(book_id, {"title": "Unknown"})
        title = metadata.get("title", "Unknown")

        print(f"[{book_id}] {title}: Indexing...")

        try:
            chunks = index_book(book_id, content, metadata, vector_store, config)
            total_chunks += chunks
            print(f"[{book_id}] {title}: Created {chunks} chunks")
        except Exception as e:
            print(f"[{book_id}] {title}: Indexing failed: {e}", file=sys.stderr)

    print(f"\nTotal chunks indexed: {total_chunks}")


if __name__ == "__main__":
    main()
