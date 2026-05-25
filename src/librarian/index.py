"""Index extracted books into Qdrant vector store."""

import fcntl
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from pylatexenc.latex2text import LatexNodes2Text

# Shared LaTeX to text converter
_latex_converter = LatexNodes2Text()


def augment_latex_equations(text: str) -> str:
    """Augment LaTeX equations with searchable natural language.

    Uses pylatexenc to convert LaTeX to unicode text, then adds
    a bracketed description after each equation to improve
    vector search retrieval for mathematical content.
    """
    def augment_match(match: re.Match) -> str:
        latex = match.group(1)
        try:
            description = _latex_converter.latex_to_text(latex)
            # Clean up whitespace
            description = re.sub(r"\s+", " ", description).strip()
            if description:
                return f"{match.group(0)}\n[Mathematical equation: {description}]"
        except Exception:
            pass  # If conversion fails, just return original
        return match.group(0)

    # Match display equations $$...$$
    augmented = re.sub(r"\$\$(.+?)\$\$", augment_match, text, flags=re.DOTALL)
    return augmented

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex

# Lock for serializing Qdrant access (local storage doesn't support concurrent clients)
QDRANT_LOCK = Path("/tmp/librarian-qdrant.lock")
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore

from librarian.config import expand_path, load_config
from librarian.metadata_types import (
    META_BLOCK_INDEX,
    META_BREADCRUMB,
    META_CHAPTER_NUM,
    META_CHAPTER_TITLE,
    META_PAGE,
    META_SECTION_TITLE,
    build_base_node_metadata,
    build_chapter_node_metadata,
    serialize_list_metadata as contract_serialize_list_metadata,
    with_block_metadata,
)
from librarian.files import marker_content_json, marker_markdown
from librarian import calibre
from librarian.vectorstore import get_vector_store, get_collection_names
from librarian.equations import (
    ExtractedEquation,
    extract_equations,
    extract_equations_from_blocks,
    prepare_equation_documents,
    EquationAwareChunker,
)
from librarian.structure import (
    DocumentStructure,
    parse_structure,
    extract_structure_from_blocks,
    validate_structure,
    get_context_for_page,
    get_chapter_for_block,
)


def extract_page_number(text: str) -> int | None:
    """Extract page number from Marker's embedded page markers."""
    # Look for <span id="page-XXX"> or image refs like _page_XXX_
    page_spans = re.findall(r'page-(\d+)', text)
    page_images = re.findall(r'_page_(\d+)_', text)
    pages = page_spans + page_images
    if pages:
        return int(pages[0])  # Return first page found
    return None


def get_calibre_metadata(library_path: Path, max_retries: int = 3) -> dict[int, dict]:
    """Get book metadata from Calibre for all books.

    Retries with exponential backoff if Calibre database is locked.
    """
    cmd = [
        "calibredb", "list",
        "--library-path", str(library_path),
        "--fields", "id,title,authors,tags,publisher,pubdate,*subjects,*status,formats",
        "--for-machine",
    ]

    for attempt in range(max_retries):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            break

        # Check if it's a lock contention error (retryable)
        if "Another calibre program" in result.stderr:
            delay = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
            print(f"Calibre busy, retrying in {delay}s...", file=sys.stderr)
            time.sleep(delay)
            continue

        # Non-retryable error
        print(f"Error querying Calibre: {result.stderr}", file=sys.stderr)
        return {}
    else:
        print(f"Calibre unavailable after {max_retries} retries", file=sys.stderr)
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

        # Get best source file path (prefer PDF for page linking)
        formats = book.get("formats", [])
        source_path = None
        for fmt in formats:
            if fmt.lower().endswith(".pdf"):
                source_path = fmt
                break
        if not source_path and formats:
            source_path = formats[0]  # Fallback to first available
        book["source_path"] = source_path

    return {book["id"]: book for book in books}


def load_extracted_book(book_dir: Path) -> tuple[str | None, str | None]:
    """Load the extracted markdown for a book.

    Returns:
        Tuple of (augmented_content, raw_content) or (None, None) if not found.
        - augmented_content: LaTeX equations have natural language descriptions
        - raw_content: Original markdown for equation extraction
    """
    md_file = marker_markdown(book_dir)
    if not md_file:
        return None, None
    raw_content = md_file.read_text()
    augmented_content = augment_latex_equations(raw_content)
    return augmented_content, raw_content


def load_extracted_blocks(book_dir: Path) -> list[dict] | None:
    """Load structured blocks from marker JSON output.

    Returns list of blocks with text and metadata, or None if not available.
    Each block contains:
        - text: Plain text content (converted from HTML)
        - page: Page number
        - block_type: SectionHeader, Text, Table, etc.
        - block_id: Unique identifier
    """
    import markdownify

    content_file = marker_content_json(book_dir)
    if not content_file:
        return None

    with open(content_file) as f:
        data = json.load(f)

    # Handle both "blocks" and "chunks" keys
    raw_blocks = data.get("blocks", data.get("chunks", []))
    if not raw_blocks:
        return None

    blocks = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue

        # Convert HTML to markdown/text
        html = block.get("html", "")
        if html:
            text = markdownify.markdownify(html, heading_style="ATX").strip()
        else:
            text = block.get("text", "")

        if not text:
            continue

        blocks.append({
            "text": text,
            "page": block.get("page"),
            "block_type": block.get("block_type", "Text"),
            "block_id": block.get("id", ""),
        })

    return blocks if blocks else None


def serialize_list_metadata(value) -> str:
    """Serialize list metadata to JSON string for backend compatibility.

    LanceDB doesn't support list values in metadata, so we serialize
    lists to JSON strings. This works with all backends.
    """
    return contract_serialize_list_metadata(value)


def create_documents(
    book_id: int,
    content: str,
    metadata: dict,
) -> list[Document]:
    """Create LlamaIndex documents with metadata."""
    doc_metadata = build_base_node_metadata(book_id=book_id, metadata=metadata)

    # Create a single document - chunking happens via node parser
    return [Document(text=content, metadata=doc_metadata)]


def _clean_code_text(text: str) -> str:
    """Remove PDF line number artifacts from code blocks.

    Handles two patterns marker produces from PDFs with line numbers:
    1. Standalone lines: just a number on its own line (e.g., CTCI verbose blocks)
    2. Inline prefixes: number at start of code line (e.g., "53 }" or "56 ArrayList<>")

    Both are detected via sequential number patterns and stripped.
    """
    lines = text.split("\n")

    # Pattern 1: standalone number lines
    standalone_nums = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^\d{1,4}$", stripped):
            standalone_nums.append((i, int(stripped)))

    if len(standalone_nums) >= 3 and len(standalone_nums) > len(lines) * 0.15:
        nums = [n for _, n in standalone_nums]
        diffs = [nums[i + 1] - nums[i] for i in range(len(nums) - 1)]
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        if 0 < avg_diff < 5:
            remove = {i for i, _ in standalone_nums}
            lines = [l for i, l in enumerate(lines) if i not in remove]
            # Collapse runs of blank lines left behind
            cleaned = []
            for line in lines:
                if line.strip() == "" and cleaned and cleaned[-1].strip() == "":
                    continue
                cleaned.append(line)
            return "\n".join(cleaned)

    # Pattern 2: inline number prefixes ("53 }", "56 ArrayList<String>")
    prefix_nums = []
    for i, line in enumerate(lines):
        m = re.match(r"^(\d{1,4}) (\S)", line)
        if m:
            prefix_nums.append((i, int(m.group(1))))

    if len(prefix_nums) >= 2 and len(prefix_nums) > len(lines) * 0.1:
        nums = [n for _, n in prefix_nums]
        diffs = [nums[i + 1] - nums[i] for i in range(len(nums) - 1)]
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        if 0 < avg_diff < 5:
            for i, _ in prefix_nums:
                lines[i] = re.sub(r"^\d{1,4} ", "", lines[i])
            return "\n".join(lines)

    return text


def create_nodes_from_blocks(
    blocks: list[dict],
    book_id: int,
    metadata: dict,
    chunk_size: int = 512,
) -> list[TextNode]:
    """Create LlamaIndex nodes from marker JSON blocks.

    Each block becomes a node with page number and block type metadata.
    Large blocks are split to respect chunk_size.

    Args:
        blocks: List of block dicts from load_extracted_blocks
        book_id: Calibre book ID
        metadata: Book metadata from Calibre
        chunk_size: Max characters per node (large blocks are split)

    Returns:
        List of TextNode objects ready for indexing.
        Each node has _block_idx in metadata for chapter lookup.
    """
    base_metadata = build_base_node_metadata(book_id=book_id, metadata=metadata)

    nodes = []
    for block_idx, block in enumerate(blocks):
        text = block["text"]
        page = block.get("page")
        block_type = block.get("block_type", "Text")

        # Code blocks: clean line number artifacts, never split
        if block_type == "Code":
            text = _clean_code_text(text)
            node_meta = with_block_metadata(
                base_metadata,
                page=page,
                block_type=block_type,
                block_idx=block_idx,
            )
            nodes.append(TextNode(text=text, metadata=node_meta))
            continue

        # Split large text blocks on paragraphs
        if len(text) > chunk_size * 1.5:
            parts = text.split("\n\n")
            current_chunk = ""
            for part in parts:
                if len(current_chunk) + len(part) > chunk_size and current_chunk:
                    node_meta = with_block_metadata(
                        base_metadata,
                        page=page,
                        block_type=block_type,
                        block_idx=block_idx,
                    )
                    nodes.append(TextNode(text=current_chunk.strip(), metadata=node_meta))
                    current_chunk = part
                else:
                    current_chunk += "\n\n" + part if current_chunk else part
            if current_chunk.strip():
                node_meta = with_block_metadata(
                    base_metadata,
                    page=page,
                    block_type=block_type,
                    block_idx=block_idx,
                )
                nodes.append(TextNode(text=current_chunk.strip(), metadata=node_meta))
        else:
            node_meta = with_block_metadata(
                base_metadata,
                page=page,
                block_type=block_type,
                block_idx=block_idx,
            )
            nodes.append(TextNode(text=text, metadata=node_meta))

    return nodes


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




def generate_chapter_summary(chapter_text: str, chapter_title: str, config: dict) -> str:
    """Generate a summary for a chapter using the configured LLM.

    Uses the same LLM provider configured for classification.
    """
    import httpx

    llm_config = config.get("classification", {})
    provider = llm_config.get("provider", "ollama")
    model = llm_config.get("model", "llama3.2")

    # Truncate chapter text if too long (keep first ~4000 chars for summary)
    max_chars = 4000
    if len(chapter_text) > max_chars:
        chapter_text = chapter_text[:max_chars] + "..."

    prompt = f"""Summarize the following chapter in 2-3 sentences. Focus on the main topics and key concepts covered.

Chapter: {chapter_title}

Content:
{chapter_text}

Summary:"""

    if provider == "anthropic":
        import os
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            response = client.messages.create(
                model=model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except Exception as e:
            print(f"Anthropic summary error: {e}", file=sys.stderr)
            return ""
    else:
        # Ollama
        try:
            response = httpx.post(
                "http://localhost:11434/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=60.0,
            )
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception as e:
            print(f"Ollama summary error: {e}", file=sys.stderr)
            return ""


def extract_chapter_text(content: str, chapter_num: int, structure: DocumentStructure) -> str:
    """Extract the full text of a chapter from the document.

    Uses page markers and structure to find chapter boundaries.
    """
    chapter = structure.get_chapter(chapter_num)
    if not chapter:
        return ""

    # Find chapter start pattern
    start_pattern = re.compile(
        rf'^#\s+\*{{0,2}}Chapter\s+{chapter_num}\b',
        re.IGNORECASE | re.MULTILINE
    )
    start_match = start_pattern.search(content)
    if not start_match:
        return ""

    start_pos = start_match.start()

    # Find next chapter or end
    next_chapter = structure.get_chapter(chapter_num + 1)
    if next_chapter:
        next_pattern = re.compile(
            rf'^#\s+\*{{0,2}}Chapter\s+{chapter_num + 1}\b',
            re.IGNORECASE | re.MULTILINE
        )
        next_match = next_pattern.search(content, start_pos + 1)
        if next_match:
            return content[start_pos:next_match.start()]

    # If no next chapter, look for end markers
    end_patterns = [
        r'^#\s+\*{0,2}Appendix',
        r'^#\s+\*{0,2}Index\b',
        r'^#\s+\*{0,2}Bibliography',
        r'^#\s+\*{0,2}About the Author',
    ]
    for pattern in end_patterns:
        end_match = re.search(pattern, content[start_pos:], re.IGNORECASE | re.MULTILINE)
        if end_match:
            return content[start_pos:start_pos + end_match.start()]

    # Return to end of document
    return content[start_pos:]


def index_chapters(
    structure: DocumentStructure,
    content: str,
    metadata: dict,
    chapter_store: QdrantVectorStore,
    config: dict,
) -> int:
    """Index chapter summaries to the chapter collection.

    Creates one document per chapter with:
    - LLM-generated summary as searchable text
    - Chapter metadata (number, title, page range, section titles)
    """
    if not structure.chapters:
        return 0

    nodes = []
    for chapter in structure.chapters:
        # Extract chapter text and generate summary
        chapter_text = extract_chapter_text(content, chapter.number, structure)
        if not chapter_text:
            continue

        summary = generate_chapter_summary(chapter_text, chapter.title, config)
        if not summary:
            # Fallback: use first paragraph as summary
            paragraphs = [p.strip() for p in chapter_text.split('\n\n') if p.strip() and not p.startswith('#')]
            summary = paragraphs[0][:500] if paragraphs else ""

        chapter.summary = summary

        # Build searchable text: summary + section titles for better retrieval
        section_titles = [s.title for s in chapter.sections]
        searchable_text = f"{summary}\n\nSections: {', '.join(section_titles)}" if section_titles else summary

        # Build page range string
        page_range = ""
        if chapter.page_start:
            if chapter.page_end:
                page_range = f"{chapter.page_start}-{chapter.page_end}"
            else:
                page_range = f"{chapter.page_start}+"

        node = TextNode(
            text=searchable_text,
            metadata=build_chapter_node_metadata(
                metadata=metadata,
                chapter_num=chapter.number,
                chapter_title=chapter.title,
                summary=summary,
                page_range=page_range,
                section_titles=section_titles,
            ),
        )
        nodes.append(node)

    if not nodes:
        return 0

    # Index to chapter store
    storage_context = StorageContext.from_defaults(vector_store=chapter_store)
    VectorStoreIndex(
        nodes=nodes,
        storage_context=storage_context,
        show_progress=False,
    )

    return len(nodes)


def index_equations(
    equations: list[ExtractedEquation],
    metadata: dict,
    equation_store: QdrantVectorStore,
) -> int:
    """Index extracted equations to the equation collection.

    Each equation becomes a searchable document with:
    - Searchable text (description + context keywords)
    - Full LaTeX for display
    - Context window for understanding
    - Classification tags
    """
    if not equations:
        return 0

    # Prepare equation documents
    eq_docs = prepare_equation_documents(equations, metadata)

    # Convert to LlamaIndex nodes
    nodes = []
    for doc in eq_docs:
        node = TextNode(
            text=doc["text"],
            metadata=doc["metadata"],
        )
        nodes.append(node)

    # Index to equation store
    storage_context = StorageContext.from_defaults(vector_store=equation_store)
    VectorStoreIndex(
        nodes=nodes,
        storage_context=storage_context,
        show_progress=False,  # Equations are usually few, no need for progress
    )

    return len(nodes)


def index_book(
    book_id: int,
    content: str,
    raw_content: str,
    metadata: dict,
    vector_store: QdrantVectorStore,
    equation_store: QdrantVectorStore | None,
    chapter_store: QdrantVectorStore | None,
    config: dict,
    blocks: list[dict] | None = None,
    progress_fn: callable = None,
) -> tuple[int, int, int]:
    """Index a single book and return (text_chunks, equation_count, chapter_count).

    Args:
        book_id: Calibre book ID
        content: Augmented markdown (with equation descriptions)
        raw_content: Original markdown (for equation extraction)
        metadata: Calibre metadata
        vector_store: Main text chunk store
        equation_store: Separate equation store (or None to skip)
        chapter_store: Chapter summary store (or None to skip)
        config: Application config
        blocks: Optional JSON blocks from marker (preferred for page metadata)
        progress_fn: Optional callback(done, total, message) for progress reporting

    Returns:
        Tuple of (text_chunk_count, equation_count, chapter_count)
    """
    book_title = metadata.get("title", "Unknown")

    # Parse document structure for hierarchical metadata
    # PRIMARY: Use JSON blocks (reliable page numbers from PDF)
    # FALLBACK: Parse markdown (may have scrambled page markers)
    if blocks:
        structure = extract_structure_from_blocks(blocks, title=book_title)
        structure_source = "blocks"
    else:
        structure = parse_structure(raw_content, title=book_title)
        structure_source = "markdown"
        print(f"  [FALLBACK] Using markdown for structure (no JSON blocks)", file=sys.stderr)

    # Validate structure and warn if issues
    total_pages = max((b.get('page') or 0) for b in blocks) if blocks else None
    validation = validate_structure(structure, total_pages)
    if validation["warnings"]:
        for warning in validation["warnings"]:
            print(f"  [WARNING] Structure: {warning}", file=sys.stderr)
    if validation["chapter_count"] > 0:
        print(f"  Structure ({structure_source}): {validation['chapter_count']} chapters detected")

    # Extract equations - prefer JSON blocks (has proper equation markup)
    if blocks:
        equations = extract_equations_from_blocks(blocks)
    else:
        equations = extract_equations(raw_content)
    eq_count = 0

    if equations and equation_store:
        # Add book metadata to equations
        for eq in equations:
            eq.book_id = book_id
            eq.title = book_title

        eq_count = index_equations(equations, metadata, equation_store)

    # Index chapter summaries
    ch_count = 0
    if chapter_store and structure.chapters:
        ch_count = index_chapters(structure, raw_content, metadata, chapter_store, config)

    # Set up chunking config
    chunk_config = config.get("chunking", {})
    chunk_size = chunk_config.get("chunk_size", 512)
    chunk_overlap = chunk_config.get("chunk_overlap", 50)

    # Prefer JSON blocks (has page numbers from marker)
    if blocks:
        nodes = create_nodes_from_blocks(blocks, book_id, metadata, chunk_size)
        # Use BLOCK INDEX for chapter assignment (page numbers may be scrambled)
        for node in nodes:
            block_idx = node.metadata.pop(META_BLOCK_INDEX, None)  # Remove internal field

            # Look up chapter by block index (more reliable than page)
            chapter = get_chapter_for_block(structure, block_idx) if block_idx is not None else None

            if chapter:
                node.metadata[META_CHAPTER_NUM] = chapter.number
                node.metadata[META_CHAPTER_TITLE] = chapter.title
                node.metadata[META_SECTION_TITLE] = ""  # TODO: section tracking
                node.metadata[META_BREADCRUMB] = chapter.breadcrumb
            else:
                # Fallback to page-based lookup
                page = node.metadata.get(META_PAGE)
                context = get_context_for_page(structure, page)
                node.metadata[META_CHAPTER_NUM] = context["chapter_num"]
                node.metadata[META_CHAPTER_TITLE] = context["chapter_title"]
                node.metadata[META_SECTION_TITLE] = context["section_title"]
                node.metadata[META_BREADCRUMB] = context["breadcrumb"]
    else:
        # Fallback: use markdown content with text-based chunking
        documents = create_documents(book_id, content, metadata)

        if equations:
            # Use equation-aware chunking to keep equations with context
            chunker = EquationAwareChunker(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            chunks = chunker.chunk(content)

            # Convert to LlamaIndex nodes
            nodes = []
            for chunk in chunks:
                node = TextNode(
                    text=chunk["text"],
                    metadata=documents[0].metadata.copy(),
                )
                nodes.append(node)
        else:
            # Standard chunking for non-math content
            node_parser = SentenceSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            nodes = node_parser.get_nodes_from_documents(documents)

        # Extract page numbers from text and add hierarchical context
        last_known_page = None
        for node in nodes:
            page = extract_page_number(node.text)
            if page:
                last_known_page = page
            node.metadata[META_PAGE] = last_known_page

            # Add hierarchical context from document structure
            context = get_context_for_page(structure, last_known_page)
            node.metadata[META_CHAPTER_NUM] = context["chapter_num"]
            node.metadata[META_CHAPTER_TITLE] = context["chapter_title"]
            node.metadata[META_SECTION_TITLE] = context["section_title"]
            node.metadata[META_BREADCRUMB] = context["breadcrumb"]

    # Validate chapter coverage in nodes
    nodes_with_chapter = sum(1 for n in nodes if n.metadata.get(META_CHAPTER_NUM))
    coverage = nodes_with_chapter / len(nodes) if nodes else 0
    if coverage < 0.5 and len(nodes) > 10:
        print(f"  [WARNING] Only {coverage:.0%} of chunks have chapter metadata", file=sys.stderr)
    elif nodes_with_chapter > 0:
        print(f"  Chapter coverage: {coverage:.0%} ({nodes_with_chapter}/{len(nodes)} chunks)")

    # Create storage context with vector store
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    # Embed and store in batches so we can report progress
    batch_size = 200
    total = len(nodes)
    for i in range(0, total, batch_size):
        batch = nodes[i : i + batch_size]
        VectorStoreIndex(
            nodes=batch,
            storage_context=storage_context,
            show_progress=True,
        )
        done = min(i + batch_size, total)
        if progress_fn:
            progress_fn(done, total, f"Embedded {done}/{total} chunks")

    return total, eq_count, ch_count




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

    # Get vector store
    store = get_vector_store(config)

    # Conditional locking - only needed for file-based backends
    if store.requires_lock():
        with open(QDRANT_LOCK, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                _run_indexing(args, config, calibre_metadata, output_path, store, library_path)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    else:
        _run_indexing(args, config, calibre_metadata, output_path, store, library_path)


def _run_indexing(args: dict, config: dict, calibre_metadata: dict, output_path: Path, store, library_path: Path):
    """Run the indexing pipeline.

    Implements triple indexing:
    1. Text chunks → main collection (librarian_full)
    2. Equations → equation collection (librarian_equations)
    3. Chapter summaries → chapter collection (librarian_chapters)

    Only indexes books with *status='extracted' (unless --force).
    Updates Calibre *status to 'indexed' after successful indexing.

    Args:
        args: Parsed command line arguments
        config: Application configuration
        calibre_metadata: Metadata for all books from Calibre
        output_path: Path to extracted book content
        store: Vector store backend (LibrarianVectorStore)
        library_path: Calibre library path for status updates
    """
    # Get collection names
    collections = get_collection_names(config)
    collection = collections["full"]
    equation_collection = collections["equations"]
    chapter_collection = collections["chapters"]

    # Get LlamaIndex stores for each collection
    vector_store = store.get_llama_store(collection)
    equation_store = store.get_llama_store(equation_collection)
    chapter_store = store.get_llama_store(chapter_collection)

    # Get already indexed books from vector store
    indexed_in_store = set() if args["force"] else store.get_indexed_ids(collection)

    # Find extracted books (directories with content)
    extracted_dirs = [d for d in output_path.iterdir() if d.is_dir() and d.name.isdigit()]

    # Filter to books needing indexing based on Calibre status
    books_to_index = []
    for book_dir in sorted(extracted_dirs, key=lambda d: int(d.name)):
        book_id = int(book_dir.name)

        # Filter by book ID if specified
        if args["book_ids"] and book_id not in args["book_ids"]:
            continue

        metadata = calibre_metadata.get(book_id, {})
        status = metadata.get("*status")

        # Skip if already indexed (unless forcing)
        if not args["force"]:
            if book_id in indexed_in_store:
                continue
            # Only index books with status='extracted' (or legacy books with no status)
            if status not in (None, "extracted"):
                continue

        books_to_index.append((book_id, book_dir, metadata))

    if not books_to_index:
        print("No books need indexing")
        return

    print(f"Found {len(books_to_index)} books to index")

    total_chunks = 0
    total_equations = 0
    total_chapters = 0

    for book_id, book_dir, metadata in books_to_index:
        title = metadata.get("title", "Unknown")
        metadata["id"] = book_id  # Ensure ID is in metadata for equations

        # Load content (both augmented and raw)
        content, raw_content = load_extracted_book(book_dir)
        if not content:
            print(f"[{book_id}] No extracted content found, skipping")
            continue

        # Try to load JSON blocks (preferred - has page numbers)
        blocks = load_extracted_blocks(book_dir)

        source_type = "blocks" if blocks else "markdown"
        print(f"[{book_id}] {title}: Indexing from {source_type}...")

        # If force re-indexing, delete old entries first
        if args["force"]:
            for coll in [collection, equation_collection, chapter_collection]:
                store.delete_by_filter(coll, "book_id", book_id)

        try:
            chunks, eq_count, ch_count = index_book(
                book_id, content, raw_content, metadata,
                vector_store, equation_store, chapter_store, config,
                blocks=blocks
            )
            total_chunks += chunks
            total_equations += eq_count
            total_chapters += ch_count

            # Update pipeline status to indexed
            calibre.set_status(book_id, "indexed", library_path)

            # Build status message
            parts = [f"{chunks} chunks"]
            if eq_count:
                parts.append(f"{eq_count} equations")
            if ch_count:
                parts.append(f"{ch_count} chapters")
            print(f"[{book_id}] {title}: Created {' + '.join(parts)}")
        except Exception as e:
            print(f"[{book_id}] {title}: Indexing failed: {e}", file=sys.stderr)

    print(f"\nTotal indexed: {total_chunks} chunks, {total_equations} equations, {total_chapters} chapters")


if __name__ == "__main__":
    main()
