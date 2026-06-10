"""Index extracted books into vector store.

Pure business logic. Functions here take
content and metadata, create nodes, and write to vector stores.

The MCP server's index worker is the caller (librarian.mcp_server).
"""

import json
import re
import sys
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

from llama_index.core import Document, StorageContext, VectorStoreIndex

from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from llama_index.vector_stores.qdrant import QdrantVectorStore

from librarian.embeddings import get_embed_model
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
    get_hierarchy_for_block,
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
        - html: Original block HTML (kept so equation extraction can recover
          clean LaTeX from <math> markup; marker leaves text empty for equations)
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
            "html": html,
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
    chunk_size: int = 320,
    chunk_overlap: int = 64,
) -> list[TextNode]:
    """Create LlamaIndex nodes from marker JSON blocks.

    Each block becomes one or more nodes carrying page/block_type/block_idx
    metadata. Oversized blocks are split with a TOKEN-aware splitter so no node
    exceeds the embedding model's sequence limit (BGE truncates >512 tokens) —
    this applies to every block type, including Code, so long listings aren't
    silently truncated at embed time. Sub-chunks of a block keep that block's
    metadata, so chapter/section lookup by block_idx still resolves correctly.

    Args:
        blocks: List of block dicts from load_extracted_blocks
        book_id: Book ID
        metadata: Book metadata
        chunk_size: Max tokens per node (oversized blocks are split)
        chunk_overlap: Token overlap between sub-chunks of a split block

    Returns:
        List of TextNode objects ready for indexing.
        Each node has _block_idx in metadata for chapter/section lookup.
    """
    base_metadata = build_base_node_metadata(book_id=book_id, metadata=metadata)
    splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    nodes = []
    for block_idx, block in enumerate(blocks):
        text = block["text"]
        block_type = block.get("block_type", "Text")
        if block_type == "Code":
            text = _clean_code_text(text)
        if not text or not text.strip():
            continue

        page = block.get("page")
        # split_text returns [text] when already within budget, multiple pieces
        # (with overlap) when the block is too large for one embedding.
        for piece in splitter.split_text(text):
            node_meta = with_block_metadata(
                base_metadata,
                page=page,
                block_type=block_type,
                block_idx=block_idx,
            )
            nodes.append(TextNode(text=piece, metadata=node_meta))

    return nodes


def generate_chapter_summary(
    chapter_text: str, chapter_title: str, config: dict, unit: str = "chapter",
) -> str:
    """Generate a summary for a chapter or section using the configured LLM.

    Uses the same LLM provider configured for classification.
    """
    from librarian.llm import complete

    # Truncate text if too long (keep first ~4000 chars for summary)
    max_chars = 4000
    if len(chapter_text) > max_chars:
        chapter_text = chapter_text[:max_chars] + "..."

    prompt = f"""Summarize the following {unit} in 2-3 sentences. Focus on the main topics and key concepts covered.

{unit.capitalize()}: {chapter_title}

Content:
{chapter_text}

Summary:"""

    return complete(prompt, config, max_tokens=256, timeout=60.0).strip()


def _sample_text(content: str, sample_size: int = 5000) -> str:
    """Begin/middle/end sample of a document (for whole-book summarization)."""
    if len(content) <= sample_size:
        return content
    chunk = sample_size // 3
    middle_start = (len(content) - chunk) // 2
    return (
        f"{content[:chunk]}\n\n[...]\n\n"
        f"{content[middle_start:middle_start + chunk]}\n\n[...]\n\n"
        f"{content[-chunk:]}"
    )


def generate_book_summary(content: str, title: str, config: dict) -> str:
    """Generate a whole-book summary from a content sample."""
    from librarian.llm import complete

    prompt = f"""Summarize this book in 3-5 sentences: what it is about, what it covers, and who/what it is useful for. Base the summary only on the sample below.

Book title: {title}

Content sample (beginning / middle / end):
---
{_sample_text(content)}
---

Summary:"""

    return complete(prompt, config, max_tokens=350, timeout=60.0).strip()


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


# Section-summary guardrails: skip fragments, and cap LLM calls per book.
MIN_SECTION_SUMMARY_CHARS = 1500
MAX_SECTION_SUMMARIES = 40


def _build_chapter_summary_nodes(
    structure: DocumentStructure,
    content: str,
    metadata: dict,
    config: dict,
) -> list[TextNode]:
    """One summary node per detected chapter (level=chapter)."""
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
    return nodes


def _section_texts(
    structure: DocumentStructure, blocks: list[dict],
) -> list[tuple[str, str]]:
    """Ordered (section_title, concatenated_text) pairs from the block map."""
    grouped: dict[str, list[str]] = {}
    for idx, block in enumerate(blocks):
        title = structure.block_to_section.get(idx)
        if not title:
            continue
        text = block.get("text", "")
        if text:
            grouped.setdefault(title, []).append(text)
    return [(title, "\n\n".join(parts)) for title, parts in grouped.items()]


def _build_section_summary_nodes(
    structure: DocumentStructure,
    blocks: list[dict],
    metadata: dict,
    config: dict,
) -> list[TextNode]:
    """One summary node per substantial section (level=section).

    Used when a book has sections but no chapters (articles, manuals,
    web captures). Tiny sections are skipped and the per-book LLM call
    count is capped.
    """
    candidates = [
        (title, text)
        for title, text in _section_texts(structure, blocks)
        if len(text) >= MIN_SECTION_SUMMARY_CHARS
    ]
    if len(candidates) > MAX_SECTION_SUMMARIES:
        print(
            f"  [summaries] capping section summaries at {MAX_SECTION_SUMMARIES} "
            f"(book has {len(candidates)} substantial sections)",
            file=sys.stderr,
        )
        candidates = candidates[:MAX_SECTION_SUMMARIES]

    nodes = []
    for title, text in candidates:
        summary = generate_chapter_summary(text, title, config, unit="section")
        if not summary:
            continue
        node_meta = build_chapter_node_metadata(
            metadata=metadata,
            chapter_num=None,
            chapter_title=title,
            summary=summary,
            page_range="",
            section_titles=[],
            level="section",
        )
        node_meta[META_SECTION_TITLE] = title
        nodes.append(TextNode(text=summary, metadata=node_meta))
    return nodes


def build_summary_nodes(
    structure: DocumentStructure,
    content: str,
    metadata: dict,
    config: dict,
    blocks: list[dict] | None = None,
) -> list[TextNode]:
    """Build the summary hierarchy for a book.

    - book-level summary: always (one LLM call over a content sample)
    - chapter summaries: when chapters were detected
    - section summaries: fallback when the book has sections but no chapters
    """
    nodes: list[TextNode] = []

    book_title = metadata.get("title", "Unknown")
    book_summary = generate_book_summary(content, book_title, config)
    if book_summary:
        nodes.append(TextNode(
            text=book_summary,
            metadata=build_chapter_node_metadata(
                metadata=metadata,
                chapter_num=None,
                chapter_title="",
                summary=book_summary,
                page_range="",
                section_titles=[],
                level="book",
            ),
        ))

    if structure.chapters:
        nodes.extend(_build_chapter_summary_nodes(structure, content, metadata, config))
    elif blocks and structure.block_to_section:
        nodes.extend(_build_section_summary_nodes(structure, blocks, metadata, config))

    return nodes


def index_summaries(
    structure: DocumentStructure,
    content: str,
    metadata: dict,
    chapter_store: QdrantVectorStore,
    config: dict,
    blocks: list[dict] | None = None,
) -> int:
    """Index the summary hierarchy (book/chapter/section) to the chapters collection."""
    nodes = build_summary_nodes(structure, content, metadata, config, blocks=blocks)
    if not nodes:
        return 0

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
    """Index a single book and return (text_chunks, equation_count, summary_count).

    Args:
        book_id: Book ID
        content: Augmented markdown (with equation descriptions)
        raw_content: Original markdown (for equation extraction)
        metadata: Book metadata dict
        vector_store: Main text chunk store
        equation_store: Separate equation store (or None to skip)
        chapter_store: Summary store (or None to skip). Receives the summary
            hierarchy: book-level always, plus chapter or section summaries.
        config: Application config
        blocks: Optional JSON blocks from marker (preferred for page metadata)
        progress_fn: Optional callback(done, total, message) for progress reporting

    Returns:
        Tuple of (text_chunk_count, equation_count, summary_count)
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

    # Index the summary hierarchy (book always; chapters or sections as available)
    ch_count = 0
    if chapter_store:
        ch_count = index_summaries(
            structure, raw_content, metadata, chapter_store, config, blocks=blocks,
        )

    # Set up chunking config
    chunk_config = config.get("chunking", {})
    chunk_size = chunk_config.get("chunk_size", 320)
    chunk_overlap = chunk_config.get("chunk_overlap", 64)

    # Prefer JSON blocks (has page numbers from marker)
    if blocks:
        nodes = create_nodes_from_blocks(blocks, book_id, metadata, chunk_size, chunk_overlap)
        # Use BLOCK INDEX for chapter/section assignment (page numbers may be scrambled)
        for node in nodes:
            block_idx = node.metadata.pop(META_BLOCK_INDEX, None)  # Remove internal field

            # Reading-order lookup handles chaptered books and flat articles alike.
            context = get_hierarchy_for_block(structure, block_idx)
            if context["chapter_num"] is None and not context["section_title"]:
                # Nothing found by block index — fall back to page-based lookup.
                context = get_context_for_page(structure, node.metadata.get(META_PAGE))

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




