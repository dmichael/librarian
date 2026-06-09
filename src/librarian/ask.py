"""RAG-based question answering with grounded citations."""

import re

from librarian.config import load_config
from librarian.metadata_types import (
    META_BREADCRUMB,
    META_CHAPTER_NUM,
    META_CHAPTER_TITLE,
    META_LIBRARY,
    META_PAGE,
    META_RESULT_TYPE,
    META_SECTION_TITLE,
    META_SECTION_TITLES,
    META_SOURCE_PATH,
    META_START_PAGE,
    META_TITLE,
)
from librarian.query import retrieve, retrieve_chapters_ordered, retrieve_hierarchical


def classify_query(question: str) -> str:
    """Classify query as 'structural', 'content', or 'hybrid'.

    Structural queries ask about WHERE content is located:
    - "which chapter", "what chapter"
    - "where is X explained/discussed/covered"
    - "first" + topic (first explained, first mentioned)
    - "in depth", "detailed discussion"
    - "chapter that covers"

    Content queries ask about WHAT something means:
    - "what is", "how does", "explain", "describe"
    - Direct topic questions without location qualifiers

    Hybrid queries want both location and content.
    """
    q = question.lower()

    structural_patterns = [
        "which chapter",
        "what chapter",
        "where is",
        "where does",
        "where are",
        "first explain",
        "first mention",
        "first discuss",
        "first introduced",
        "in depth",
        "in-depth",
        "detailed discussion",
        "chapter that",
        "chapter covers",
        "chapter about",
        "which section",
        "what section",
        "where can i find",
        "where can i read",
        "what should i read",
        "which part",
    ]

    content_patterns = [
        "what is",
        "what are",
        "how does",
        "how do",
        "explain",
        "describe",
        "define",
        "tell me about",
        "summarize",
    ]

    has_structural = any(pat in q for pat in structural_patterns)
    has_content = any(pat in q for pat in content_patterns)

    if has_structural and has_content:
        return "hybrid"
    elif has_structural:
        return "structural"
    else:
        return "content"


def clean_text_for_display(text: str) -> str:
    """Clean markdown artifacts for readable display."""
    # Remove image refs
    text = re.sub(r'!\[\]\([^)]+\)', '', text)
    # Remove span tags
    text = re.sub(r'<span[^>]*>', '', text)
    text = re.sub(r'</span>', '', text)
    # Clean up excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _build_pdf_link(source_path: str, page) -> str | None:
    """Build a file:// link to the source PDF, anchored to a page when known."""
    if source_path and page:
        return f"file://{source_path}#page={page}"
    if source_path:
        return f"file://{source_path}"
    return None


def _build_chapter_context(chapter_nodes: list) -> tuple[str, list]:
    """Build context and citations from chapter-level results.

    Returns:
        Tuple of (context_string, citations_list)
    """
    context_parts = []
    citations = []

    for i, node in enumerate(chapter_nodes, 1):
        title = node.metadata.get(META_TITLE, "Unknown")
        chapter_num = node.metadata.get(META_CHAPTER_NUM)
        chapter_title = node.metadata.get(META_CHAPTER_TITLE, "")
        section_titles = node.metadata.get(META_SECTION_TITLES, [])
        library_name = node.metadata.get(META_LIBRARY, "")
        start_page = node.metadata.get(META_START_PAGE)
        source_path = node.metadata.get(META_SOURCE_PATH, "")

        # Chapter summary is stored in node.text
        summary = node.text[:600] if node.text else ""

        # Build chapter location string
        ch_label = f"Chapter {chapter_num}" if chapter_num else "Chapter"
        if chapter_title:
            ch_label = f"{ch_label}: {chapter_title}"

        # Format sections for context
        sections_str = ""
        if section_titles:
            sections_str = "\n    Sections: " + ", ".join(section_titles[:5])
            if len(section_titles) > 5:
                sections_str += f", ... (+{len(section_titles) - 5} more)"

        context_parts.append(
            f"[{i}] {ch_label} (from {title})\n"
            f"    Summary: {summary}{sections_str}"
        )

        pdf_link = _build_pdf_link(source_path, start_page)

        citations.append({
            "num": i,
            "title": title,
            "page": start_page,
            "library": library_name,
            "quote": summary[:200] + "..." if len(summary) > 200 else summary,
            "score": node.score,
            "is_equation": False,
            "is_chapter": True,
            "chapter_num": chapter_num,
            "chapter_title": chapter_title,
            "section_titles": section_titles,
            "source_path": source_path,
            "pdf_link": pdf_link,
        })

    context = "\n\n".join(context_parts)
    return context, citations


def ask(
    question: str,
    config: dict | None = None,
    library: str | None = None,
    subjects: list[str] | None = None,
    top_k: int = 5,
    book_id: int | None = None,
    query_mode: str | None = None,
) -> dict:
    """Ask a question and get a synthesized answer with citations.

    Supports three query modes:
    - "content": Standard retrieval for "what is X" questions
    - "structural": Chapter-level retrieval for "where/which chapter" questions
    - "hybrid": Two-stage retrieval for questions needing both

    Args:
        question: The question to answer
        config: Optional config (loads default if not provided)
        library: Optional library to restrict search to
        subjects: Optional subject filters
        top_k: Number of passages to retrieve
        book_id: Optional book ID to restrict search to
        query_mode: Force a specific mode ("structural", "content", "hybrid")
                    If None, auto-detects from question

    Returns:
        dict with 'answer', 'citations', and 'query_type' keys
    """
    if config is None:
        config = load_config()

    # Determine query type
    query_type = query_mode if query_mode else classify_query(question)

    # 1. Retrieve based on query type
    if query_type == "structural":
        # Use chapter-level retrieval for structural queries
        nodes = retrieve_chapters_ordered(
            question,
            config,
            top_k=top_k,
            book_id=book_id,
            library=library,
            order_by="first",
        )
        if not nodes:
            return {"answer": "No relevant chapters found.", "citations": [], "query_type": query_type}

        # Build chapter-focused context
        context, citations = _build_chapter_context(nodes)

        # Structural-specific prompt
        prompt = f"""You are a knowledgeable assistant helping navigate a book's structure.
Answer the user's question about WHERE content is located based on the chapter information below.
Focus on identifying the relevant chapters and their order (first mention vs. detailed coverage).
Cite chapters using [N] notation.

CHAPTERS:
{context}

USER QUESTION: {question}

ANSWER (identify specific chapters, note progression from first mention to detailed coverage):"""

    elif query_type == "hybrid":
        # Use hierarchical retrieval for hybrid queries
        nodes = retrieve_hierarchical(
            question,
            config,
            top_k=top_k,
            subjects=subjects,
            library=library,
        )
        if not nodes:
            return {"answer": "No relevant passages found.", "citations": [], "query_type": query_type}

        # Build standard context
        context, citations = _build_content_context(nodes)

        # Hybrid prompt emphasizes both location and content
        prompt = f"""You are a knowledgeable assistant. Answer the user's question based ONLY on the provided sources.
The question asks both WHERE to find information AND WHAT the content means.
Cite sources using [N] notation. Include chapter/section references when available.

SOURCES:
{context}

USER QUESTION: {question}

ANSWER (address both location and content, use [N] citations):"""

    else:  # content (default)
        nodes = retrieve(question, config, top_k=top_k, subjects=subjects, library=library)
        if not nodes:
            return {"answer": "No relevant passages found.", "citations": [], "query_type": query_type}

        # Build standard context
        context, citations = _build_content_context(nodes)

        # Standard content prompt
        prompt = f"""You are a knowledgeable assistant. Answer the user's question based ONLY on the provided sources.
Cite sources using [N] notation where N is the source number. Be helpful and practical.
If the sources don't contain enough information to answer, say so.

SOURCES:
{context}

USER QUESTION: {question}

ANSWER (use [N] citations, be concise and grounded):"""

    # 2. Call LLM for synthesis
    from librarian.llm import complete

    answer = complete(prompt, config, max_tokens=2048)

    return {"answer": answer, "citations": citations, "query_type": query_type}


def _build_content_context(nodes: list) -> tuple[str, list]:
    """Build context and citations from content chunk results.

    Returns:
        Tuple of (context_string, citations_list)
    """
    context_parts = []
    citations = []

    for i, node in enumerate(nodes, 1):
        title = node.metadata.get(META_TITLE, "Unknown")
        page = node.metadata.get(META_PAGE)
        library_name = node.metadata.get(META_LIBRARY, "")
        result_type = node.metadata.get(META_RESULT_TYPE, "text")
        is_equation = result_type == "equation"

        # Get hierarchical context
        breadcrumb = node.metadata.get(META_BREADCRUMB, "")
        chapter_num = node.metadata.get(META_CHAPTER_NUM)
        chapter_title = node.metadata.get(META_CHAPTER_TITLE, "")
        section_title = node.metadata.get(META_SECTION_TITLE, "")

        # For equations, use context_window; for text, use node.text
        if is_equation:
            raw_text = node.metadata.get("context_window", node.text)
        else:
            raw_text = node.text

        # Clean text for context
        text = clean_text_for_display(raw_text)[:800]

        # Format differently for equations
        if is_equation:
            latex = node.metadata.get("latex", "")
            eq_num = node.metadata.get("equation_number", "")
            eq_label = f"Equation {eq_num}" if eq_num else "Equation"
            context_parts.append(f"[{i}] {eq_label} from {title}:\n$${latex}$$\n\nContext: {text}")
            quote = f"$${latex[:100]}$$" if latex else text[:200]
        else:
            # Include breadcrumb in context for better LLM understanding
            location = breadcrumb if breadcrumb else (f"p. {page}" if page else "")
            if location:
                context_parts.append(f"[{i}] ({location}):\n{text}")
            else:
                context_parts.append(f"[{i}]:\n{text}")
            quote = text[:200].replace("\n", " ").strip()
            if len(text) > 200:
                quote += "..."

        source_path = node.metadata.get(META_SOURCE_PATH, "")
        pdf_link = _build_pdf_link(source_path, page)

        citations.append({
            "num": i,
            "title": title,
            "page": page,
            "library": library_name,
            "quote": quote,
            "score": node.score,
            "is_equation": is_equation,
            "is_chapter": False,
            "latex": node.metadata.get("latex", "") if is_equation else None,
            "breadcrumb": breadcrumb,
            "chapter_num": chapter_num,
            "chapter_title": chapter_title,
            "section_title": section_title,
            "source_path": source_path,
            "pdf_link": pdf_link,
        })

    context = "\n\n---\n\n".join(context_parts)
    return context, citations


def format_response(result: dict) -> str:
    """Format the response for CLI display."""
    lines = []
    query_type = result.get("query_type", "content")

    lines.append("=" * 70)
    lines.append("ANSWER")
    lines.append("=" * 70)
    lines.append("")
    lines.append(result["answer"])
    lines.append("")

    # Use different header for structural queries
    if query_type == "structural":
        lines.append("=" * 70)
        lines.append("CHAPTER PROGRESSION")
        lines.append("=" * 70)
    else:
        lines.append("=" * 70)
        lines.append("REFERENCES")
        lines.append("=" * 70)

    for c in result["citations"]:
        is_eq = c.get("is_equation", False)
        is_chapter = c.get("is_chapter", False)
        lib_str = f" [{c['library']}]" if c.get("library") else ""

        if is_chapter:
            # Format chapter reference
            ch_num = c.get("chapter_num")
            ch_title = c.get("chapter_title", "")
            ch_label = f"Chapter {ch_num}" if ch_num else "Chapter"
            if ch_title:
                ch_label = f"{ch_label}: {ch_title}"

            page_str = f"p. {c['page']}" if c.get("page") else ""
            pdf_link = c.get("pdf_link", "")
            section_titles = c.get("section_titles", [])

            lines.append(f"\n[{c['num']}] {ch_label}{lib_str}")
            lines.append(f"    {c['title']}")
            if page_str:
                lines.append(f"    {page_str}")
            if pdf_link:
                lines.append(f"    {pdf_link}")
            if section_titles:
                sections_preview = ", ".join(section_titles[:3])
                if len(section_titles) > 3:
                    sections_preview += f", ... (+{len(section_titles) - 3} more)"
                lines.append(f"    Sections: {sections_preview}")
            lines.append(f'    "{c["quote"]}"')

        elif is_eq:
            # Format equation reference
            lines.append(f"\n[{c['num']}] EQUATION from {c['title']}{lib_str}")
            if c.get("latex"):
                lines.append(f"    $${c['latex'][:100]}{'...' if len(c.get('latex', '')) > 100 else ''}$$")
        else:
            # Format text reference with breadcrumb prioritized over page
            breadcrumb = c.get("breadcrumb", "")
            page_str = f"p. {c['page']}" if c.get("page") else ""
            pdf_link = c.get("pdf_link", "")

            lines.append(f"\n[{c['num']}] {c['title']}{lib_str}")

            # Prioritize breadcrumb (chapter/section) - works across all formats
            if breadcrumb:
                lines.append(f"    {breadcrumb}")
                if page_str:
                    lines.append(f"    (PDF {page_str})")
            elif page_str:
                lines.append(f"    PDF {page_str}")
            else:
                lines.append(f"    (location unknown)")

            if pdf_link:
                lines.append(f"    {pdf_link}")
            lines.append(f'    "{c["quote"]}"')

    return "\n".join(lines)


def parse_args():
    """Parse command line arguments."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="librarian-ask",
        description="Ask a question and get a synthesized answer grounded in your library.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Query modes (auto-detected if not specified):
  structural - For 'which chapter', 'where is X explained' questions
  content    - For 'what is', 'how does', 'explain' questions (default)
  hybrid     - For questions needing both location and content

Examples:
  librarian-ask --library therapy 'How do I cope when overwhelmed?'
  librarian-ask 'What is wise mind?'
  librarian-ask --structural 'which chapter covers subadvisories'
  librarian-ask --book-id 32 'where is hedge fund regulation first discussed'""",
    )
    parser.add_argument("question", nargs="+", help="The question to ask")
    parser.add_argument("--library", help="Restrict to a specific library")
    parser.add_argument("--subject", action="append", dest="subjects", default=[],
                        help="Filter by subject (repeatable, e.g. psychology/*)")
    parser.add_argument("--book-id", type=int, help="Restrict to a specific book by ID")
    parser.add_argument("--structural", action="store_const", const="structural",
                        dest="query_mode", help="Force structural query mode")
    parser.add_argument("--mode", dest="query_mode",
                        choices=["structural", "content", "hybrid"],
                        help="Force query mode")
    return parser.parse_args()


def main():
    """CLI entry point for asking questions."""
    args = parse_args()

    question = " ".join(args.question)
    config = load_config()

    # Determine query mode (auto-detect if not specified)
    detected_mode = classify_query(question)

    print(f"Question: {question}")
    if args.library:
        print(f"Library: {args.library}")
    if args.subjects:
        print(f"Subjects: {args.subjects}")
    if args.book_id:
        print(f"Book ID: {args.book_id}")
    if args.query_mode:
        print(f"Mode: {args.query_mode} (forced)")
    else:
        print(f"Mode: {detected_mode} (auto-detected)")
    print("\nSearching and synthesizing...")

    result = ask(
        question,
        config,
        library=args.library,
        subjects=args.subjects or None,
        book_id=args.book_id,
        query_mode=args.query_mode,
    )

    print(format_response(result))


if __name__ == "__main__":
    main()
