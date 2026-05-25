"""Classify books by subject using LLM analysis."""

import json
import subprocess
import sys
from pathlib import Path

from librarian.config import expand_path, load_config
from librarian.files import marker_markdown


def sample_book_content(book_dir: Path, sample_size: int = 5000) -> str:
    """Get a representative sample of book content."""
    md_file = marker_markdown(book_dir)
    if not md_file:
        return ""

    content = md_file.read_text()

    # Sample from beginning (title, TOC), middle, and end
    total_len = len(content)
    if total_len <= sample_size:
        return content

    chunk_size = sample_size // 3
    beginning = content[:chunk_size]
    middle_start = (total_len - chunk_size) // 2
    middle = content[middle_start:middle_start + chunk_size]
    end = content[-chunk_size:]

    return f"{beginning}\n\n[...]\n\n{middle}\n\n[...]\n\n{end}"


def call_ollama(prompt: str, model: str) -> str:
    """Call Ollama for classification."""
    try:
        import httpx
        response = httpx.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json().get("response", "")
    except Exception as e:
        print(f"Ollama error: {e}", file=sys.stderr)
        print("Make sure Ollama is running: ollama serve", file=sys.stderr)
        return ""


def call_anthropic(prompt: str, model: str) -> str:
    """Call Anthropic Claude for classification."""
    import os
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except ImportError:
        print("anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"Anthropic error: {e}", file=sys.stderr)
        return ""


def classify_book(
    book_id: int,
    title: str,
    content_sample: str,
    config: dict,
) -> list[str]:
    """Use LLM to suggest subjects for a book."""
    prompt = f"""Analyze this book and suggest appropriate subject classifications.

Book Title: {title}

Content Sample:
---
{content_sample}
---

Instructions:
1. Suggest 2-4 subjects that best describe this book
2. Use slash-separated format "parent/child" (e.g., "psychology/therapy", "cs/llm")
3. Return ONLY a JSON array of strings, nothing else

Example response: ["psychology/therapy", "self-help/skills-training"]

Your response (JSON array only):"""

    llm_config = config.get("classification", {})
    provider = llm_config.get("provider", "ollama")
    model = llm_config.get("model", "llama3.2")

    if provider == "anthropic":
        response = call_anthropic(prompt, model)
    else:
        response = call_ollama(prompt, model)

    if not response:
        return []

    # Parse JSON response
    try:
        # Find JSON array in response
        response = response.strip()
        start = response.find("[")
        end = response.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(response[start:end])
    except json.JSONDecodeError:
        print(f"Failed to parse LLM response: {response[:200]}", file=sys.stderr)

    return []


def get_calibre_books(library_path: Path) -> dict[int, dict]:
    """Get book metadata from Calibre."""
    cmd = [
        "calibredb", "list",
        "--library-path", str(library_path),
        "--fields", "id,title,authors,*subjects",
        "--for-machine",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {}

    books = json.loads(result.stdout)
    return {book["id"]: book for book in books}


def update_calibre_subjects(library_path: Path, book_id: int, subjects: list[str]):
    """Store subjects in Calibre custom column."""
    subjects_str = ",".join(subjects)
    cmd = [
        "calibredb", "set_custom",
        "--library-path", str(library_path),
        "subjects", str(book_id), subjects_str,
    ]
    subprocess.run(cmd, capture_output=True)


def interactive_approve(title: str, suggestions: list[str]) -> list[str]:
    """Interactive approval of suggested subjects."""
    print(f"\nSuggested subjects for '{title}':")
    for i, subj in enumerate(suggestions, 1):
        print(f"  {i}. {subj}")

    print("\nOptions:")
    print("  [Enter] Accept all suggestions")
    print("  [1,2,3] Accept specific numbers (comma-separated)")
    print("  [+topic] Add a subject (e.g., +philosophy/stoicism)")
    print("  [-] Clear and enter manually")
    print("  [s] Skip this book")

    while True:
        choice = input("\nYour choice: ").strip()

        if choice == "" or choice.lower() == "y":
            return suggestions

        if choice.lower() == "s":
            return []

        if choice == "-":
            manual = input("Enter subjects (comma-separated): ").strip()
            return [s.strip() for s in manual.split(",") if s.strip()]

        if choice.startswith("+"):
            new_subj = choice[1:].strip()
            if new_subj:
                suggestions.append(new_subj)
                print(f"Added: {new_subj}")
            continue

        # Parse number selection
        try:
            indices = [int(x.strip()) for x in choice.split(",")]
            return [suggestions[i - 1] for i in indices if 0 < i <= len(suggestions)]
        except (ValueError, IndexError):
            print("Invalid selection. Try again.")


def parse_args():
    """Parse command line arguments."""
    args = {
        "auto": False,
        "force": False,
        "book_ids": [],
    }

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--auto":
            args["auto"] = True
        elif arg == "--force":
            args["force"] = True
        elif arg == "--book-id" and i + 1 < len(sys.argv):
            args["book_ids"].append(int(sys.argv[i + 1]))
            i += 1
        elif arg in ("-h", "--help"):
            print("Usage: librarian-classify [--auto] [--force] [--book-id ID ...]")
            print("  --auto      Accept all LLM suggestions without prompting")
            print("  --force     Re-classify books that already have subjects")
            print("  --book-id   Only classify specific book IDs")
            sys.exit(0)
        i += 1

    return args


def main():
    """CLI entry point for classification."""
    args = parse_args()

    config = load_config()
    library_path = expand_path(config["library_path"])
    output_path = expand_path(config["output_path"])
    sample_size = config.get("classification", {}).get("sample_size", 5000)

    calibre_books = get_calibre_books(library_path)

    # Find extracted books
    extracted_dirs = [d for d in output_path.iterdir() if d.is_dir() and d.name.isdigit()]

    for book_dir in sorted(extracted_dirs, key=lambda d: int(d.name)):
        book_id = int(book_dir.name)

        # Filter by book ID if specified
        if args["book_ids"] and book_id not in args["book_ids"]:
            continue

        book_meta = calibre_books.get(book_id, {})
        title = book_meta.get("title", "Unknown")
        existing_subjects = book_meta.get("*subjects", "")

        # Skip if already classified (unless --force)
        if existing_subjects and not args["force"]:
            print(f"[{book_id}] {title}: Already classified ({existing_subjects}), skipping")
            continue

        print(f"\n[{book_id}] {title}: Analyzing...")

        # Sample content
        content_sample = sample_book_content(book_dir, sample_size)
        if not content_sample:
            print(f"[{book_id}] No content found, skipping")
            continue

        # Get LLM suggestions
        suggestions = classify_book(book_id, title, content_sample, config)

        if not suggestions:
            print(f"[{book_id}] No suggestions from LLM")
            if not args["auto"]:
                suggestions = interactive_approve(title, [])
            if not suggestions:
                continue

        # Auto or interactive approval
        if args["auto"]:
            approved = suggestions
            print(f"[{book_id}] Auto-approved: {approved}")
        else:
            approved = interactive_approve(title, suggestions)

        if approved:
            update_calibre_subjects(library_path, book_id, approved)
            print(f"[{book_id}] Saved subjects: {approved}")
        else:
            print(f"[{book_id}] Skipped")


if __name__ == "__main__":
    main()
