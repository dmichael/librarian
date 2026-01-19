"""RAG-based question answering with grounded citations."""

import re
import sys

import httpx

from librarian.config import expand_path, load_config
from librarian.query import retrieve


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


def call_ollama(prompt: str, model: str) -> str:
    """Call Ollama for synthesis."""
    try:
        response = httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json().get("response", "")
    except Exception as e:
        print(f"Ollama error: {e}", file=sys.stderr)
        print("Make sure Ollama is running: ollama serve", file=sys.stderr)
        return ""


def call_anthropic(prompt: str, model: str) -> str:
    """Call Anthropic Claude for synthesis."""
    import os
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except ImportError:
        print("anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"Anthropic error: {e}", file=sys.stderr)
        return ""


def ask(
    question: str,
    config: dict | None = None,
    library: str | None = None,
    subjects: list[str] | None = None,
    top_k: int = 5,
) -> dict:
    """Ask a question and get a synthesized answer with citations.

    Args:
        question: The question to answer
        config: Optional config (loads default if not provided)
        library: Optional library to restrict search to
        subjects: Optional subject filters
        top_k: Number of passages to retrieve

    Returns:
        dict with 'answer' and 'citations' keys
    """
    if config is None:
        config = load_config()

    # 1. Retrieve relevant passages
    nodes = retrieve(question, config, top_k=top_k, subjects=subjects, library=library)

    if not nodes:
        return {"answer": "No relevant passages found.", "citations": []}

    # 2. Build context and citations
    context_parts = []
    citations = []

    for i, node in enumerate(nodes, 1):
        title = node.metadata.get("title", "Unknown")
        page = node.metadata.get("page")
        library_name = node.metadata.get("library", "")
        result_type = node.metadata.get("_result_type", "text")
        is_equation = result_type == "equation"

        # Get hierarchical context
        breadcrumb = node.metadata.get("breadcrumb", "")
        chapter_num = node.metadata.get("chapter_num")
        chapter_title = node.metadata.get("chapter_title", "")
        section_title = node.metadata.get("section_title", "")

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

        # Build PDF link with page anchor
        source_path = node.metadata.get("source_path", "")
        if source_path and page:
            pdf_link = f"file://{source_path}#page={page}"
        elif source_path:
            pdf_link = f"file://{source_path}"
        else:
            pdf_link = None

        citations.append({
            "num": i,
            "title": title,
            "page": page,
            "library": library_name,
            "quote": quote,
            "score": node.score,
            "is_equation": is_equation,
            "latex": node.metadata.get("latex", "") if is_equation else None,
            "breadcrumb": breadcrumb,
            "chapter_num": chapter_num,
            "chapter_title": chapter_title,
            "section_title": section_title,
            "source_path": source_path,
            "pdf_link": pdf_link,
        })

    context = "\n\n---\n\n".join(context_parts)

    # 3. Build prompt
    prompt = f"""You are a knowledgeable assistant. Answer the user's question based ONLY on the provided sources.
Cite sources using [N] notation where N is the source number. Be helpful and practical.
If the sources don't contain enough information to answer, say so.

SOURCES:
{context}

USER QUESTION: {question}

ANSWER (use [N] citations, be concise and grounded):"""

    # 4. Call LLM for synthesis
    llm_config = config.get("classification", {})  # Reuse classification LLM config
    provider = llm_config.get("provider", "ollama")
    model = llm_config.get("model", "llama3.2")

    if provider == "anthropic":
        answer = call_anthropic(prompt, model)
    else:
        answer = call_ollama(prompt, model)

    return {"answer": answer, "citations": citations}


def format_response(result: dict) -> str:
    """Format the response for CLI display."""
    lines = []
    lines.append("=" * 70)
    lines.append("ANSWER")
    lines.append("=" * 70)
    lines.append("")
    lines.append(result["answer"])
    lines.append("")
    lines.append("=" * 70)
    lines.append("REFERENCES")
    lines.append("=" * 70)

    for c in result["citations"]:
        is_eq = c.get("is_equation", False)
        lib_str = f" [{c['library']}]" if c.get("library") else ""

        if is_eq:
            # Format equation reference
            lines.append(f"\n[{c['num']}] EQUATION from {c['title']}{lib_str}")
            if c.get("latex"):
                lines.append(f"    $${c['latex'][:100]}{'...' if len(c.get('latex', '')) > 100 else ''}$$")
        else:
            # Format text reference with breadcrumb
            breadcrumb = c.get("breadcrumb", "")
            page_str = f"p. {c['page']}" if c.get("page") else ""
            pdf_link = c.get("pdf_link", "")

            # Build location string: prefer breadcrumb, fall back to page
            if breadcrumb:
                location = breadcrumb
                if page_str:
                    location = f"{location} ({page_str})"
            else:
                location = page_str if page_str else "location unknown"

            lines.append(f"\n[{c['num']}] {c['title']}{lib_str}")
            lines.append(f"    {location}")
            if pdf_link:
                lines.append(f"    {pdf_link}")
            lines.append(f'    "{c["quote"]}"')

    return "\n".join(lines)


def parse_args():
    """Parse command line arguments."""
    args = {
        "library": None,
        "subjects": [],
        "question_parts": [],
    }

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--library" and i + 1 < len(sys.argv):
            args["library"] = sys.argv[i + 1]
            i += 1
        elif arg == "--subject" and i + 1 < len(sys.argv):
            args["subjects"].append(sys.argv[i + 1])
            i += 1
        elif arg in ("-h", "--help"):
            print("Usage: librarian-ask [--library NAME] [--subject SUBJECT ...] <question>")
            print()
            print("Ask a question and get a synthesized answer grounded in your library.")
            print()
            print("Options:")
            print("  --library   Restrict to a specific library (e.g., --library therapy)")
            print("  --subject   Filter by subject (e.g., --subject psychology/*)")
            print()
            print("Examples:")
            print("  librarian-ask --library therapy 'How do I cope when overwhelmed?'")
            print("  librarian-ask 'What is wise mind?'")
            sys.exit(0)
        else:
            args["question_parts"].append(arg)
        i += 1

    return args


def main():
    """CLI entry point for asking questions."""
    args = parse_args()

    if not args["question_parts"]:
        print("Error: No question provided")
        print("Usage: librarian-ask [--library NAME] <question>")
        sys.exit(1)

    question = " ".join(args["question_parts"])
    config = load_config()

    print(f"Question: {question}")
    if args["library"]:
        print(f"Library: {args['library']}")
    if args["subjects"]:
        print(f"Subjects: {args['subjects']}")
    print("\nSearching and synthesizing...")

    result = ask(
        question,
        config,
        library=args["library"],
        subjects=args["subjects"] if args["subjects"] else None,
    )

    print(format_response(result))


if __name__ == "__main__":
    main()
