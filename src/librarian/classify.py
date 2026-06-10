"""Classify books by subject using LLM analysis."""

import json
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

    from librarian.llm import complete

    response = complete(prompt, config, max_tokens=1024)

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


# ---------------------------------------------------------------------------
# Keyword heuristics — fast, LLM-free subject/library suggestion
# ---------------------------------------------------------------------------

TAG_RULES = [
    # therapy
    (["dialectical behavior", "dbt", "linehan", "distress tolerance", "emotion regulation"],
     "therapy/dbt"),
    (["cognitive behavioral", "cognitive behaviour", "cbt", "automatic thoughts", "thought record"],
     "therapy/cbt"),
    (["acceptance and commitment", "act ", "psychological flexibility", "defusion", "russ harris"],
     "therapy/act"),
    (["internal family systems", "ifs", "parts work", "self-energy", "richard schwartz"],
     "therapy/ifs"),
    (["existential", "logotherapy", "meaning-centered", "irvin yalom"],
     "therapy/existential"),
    (["psychotherapy", "therapeutic", "clinician", "therapist", "mental health"],
     "therapy"),
    # biology / science
    (["biology", "evolution", "ecology", "species", "organism"],
     "biology"),
    (["neuroscience", "brain", "neural", "cortex", "synapse"],
     "biology/neuroscience"),
    (["bioacoustics", "animal communication", "vocalization", "call structure"],
     "biology/bioacoustics"),
    # cs / tech
    (["algorithm", "data structure", "computer science", "programming"],
     "cs"),
    (["networking", "tcp", "protocol", "routing", "packet"],
     "cs/networking"),
    (["machine learning", "deep learning", "neural network", "training data"],
     "cs/ml"),
    (["cryptography", "bitcoin", "blockchain", "distributed ledger"],
     "cs/crypto"),
]

LIBRARY_RULES = [
    (["therapy", "therapeutic", "psychotherapy", "clinician", "mental health",
      "dbt", "cbt", "act ", "ifs", "counseling"], "therapy-core"),
    (["biology", "ecology", "evolution", "species", "organism", "bioacoustics"],
     "biology"),
    (["computer", "programming", "algorithm", "software", "networking"],
     "cs"),
]


def suggest_tags_for_text(sample: str) -> tuple[list[str], str | None]:
    """Suggest (subjects, library) for a text sample via keyword heuristics."""
    sample = sample.lower()

    matched_subjects = [
        subject for keywords, subject in TAG_RULES
        if any(kw in sample for kw in keywords)
    ]

    # Deduplicate: if we matched therapy/dbt, drop bare "therapy"
    specific = [s for s in matched_subjects if "/" in s]
    if specific:
        matched_subjects = [
            s for s in matched_subjects
            if "/" in s or not any(sp.startswith(s + "/") for sp in specific)
        ]

    suggested_library = next(
        (library for keywords, library in LIBRARY_RULES
         if any(kw in sample for kw in keywords)),
        None,
    )

    return matched_subjects, suggested_library


# ---------------------------------------------------------------------------
# LLM-backed, taxonomy-aware tagging
# ---------------------------------------------------------------------------


def gather_taxonomy(config: dict) -> tuple[list[str], list[str]]:
    """Return (subjects, libraries) currently in use, for taxonomy-aware tagging."""
    from librarian.db import Book, session_scope

    subjects: set[str] = set()
    libraries: set[str] = set()
    with session_scope(config) as session:
        for b in session.query(Book).all():
            if b.subjects:
                subjects.update(b.subjects)
            if b.library:
                libraries.add(b.library)
    return sorted(subjects), sorted(libraries)


def _parse_tag_object(response: str) -> tuple[list[str], str | None]:
    """Leniently parse the LLM's {"subjects": [...], "library": "..."} object."""
    start = response.find("{")
    end = response.rfind("}") + 1
    if start < 0 or end <= start:
        return [], None
    try:
        obj = json.loads(response[start:end])
    except json.JSONDecodeError:
        return [], None
    subjects = [
        s.strip() for s in obj.get("subjects", [])
        if isinstance(s, str) and s.strip()
    ]
    library = obj.get("library")
    library = library.strip() if isinstance(library, str) and library.strip() else None
    return subjects, library


def suggest_subjects_llm(
    title: str,
    authors: list[str],
    content_sample: str,
    subjects_taxonomy: list[str],
    libraries_taxonomy: list[str],
    config: dict,
) -> tuple[list[str], str | None]:
    """Ask the configured LLM for taxonomy-aware subjects + library."""
    from librarian.llm import complete

    existing_subjects = "\n".join(subjects_taxonomy) or "(none yet)"
    existing_libraries = ", ".join(libraries_taxonomy) or "(none yet)"

    prompt = f"""You are tagging a book to help BUILD and organize a personal research library.
The library uses a slash-separated subject taxonomy (e.g. "finance/trading",
"mathematics/dynamical-systems"). New books regularly introduce topics the
taxonomy doesn't cover yet — proposing well-formed NEW tags is expected and is
how the library grows. Do not limit yourself to the existing tags.

Book title: {title}
Authors: {', '.join(authors) or 'unknown'}

Content sample:
---
{content_sample}
---

Subjects already in use — treat these as a guide to NAMING STYLE and a way to
avoid near-duplicates, NOT as a closed list:
{existing_subjects}

Libraries (collections) already in use:
{existing_libraries}

Guidelines:
- Pick 2-4 specific subjects that describe what THIS book is actually about.
- Reuse an existing subject when one genuinely fits.
- When the book covers ground the taxonomy lacks, CREATE a new "parent/child"
  subject in the same style. Prefer extending an existing top-level parent
  (e.g. "finance/<new-child>") over inventing a new parent, unless it's clearly
  a new domain.
- Favor specific, substantive tags over generic ones.
- Choose the best-fitting existing library, or propose a new short lowercase slug.

Return ONLY a JSON object, no other text:
{{"subjects": ["parent/child", "parent/child"], "library": "slug"}}"""

    response = complete(prompt, config, max_tokens=400)
    if not response:
        return [], None
    return _parse_tag_object(response)


def suggest_tags_for_book(config: dict, book_id: int) -> dict:
    """LLM-backed, taxonomy-aware subject/library suggestion for one book.

    Falls back to keyword heuristics only if the LLM is unreachable, so the
    tool degrades instead of hard-failing.
    """
    from librarian.db import Book, session_scope

    with session_scope(config) as session:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}
        title = book.title or ""
        authors = list(book.authors or [])
        current_subjects = list(book.subjects or [])
        current_library = book.library

    sample_size = config.get("classification", {}).get("sample_size", 5000)
    output_path = expand_path(config["output_path"])
    content_sample = sample_book_content(output_path / str(book_id), sample_size)

    subjects_tax, libraries_tax = gather_taxonomy(config)

    suggested_subjects, suggested_library = suggest_subjects_llm(
        title, authors, content_sample, subjects_tax, libraries_tax, config
    )
    method = "llm"

    if not suggested_subjects:
        # LLM unreachable or unparseable — degrade to keyword heuristics.
        kw_subjects, kw_library = suggest_tags_for_text(
            " ".join([title, *authors, content_sample])
        )
        if kw_subjects or kw_library:
            suggested_subjects, suggested_library = kw_subjects, kw_library
            method = "keyword-fallback"

    return {
        "success": True,
        "book_id": book_id,
        "title": title,
        "method": method,
        "current_subjects": current_subjects,
        "current_library": current_library,
        "suggested_subjects": suggested_subjects,
        "suggested_library": suggested_library,
        "existing_taxonomy": subjects_tax,
        "hint": "Review and apply with update_book(book_id, subjects=..., library=...).",
    }


def get_books(config: dict) -> dict[int, dict]:
    """Get book metadata from the database, keyed by id."""
    from librarian.db import get_book_metadata

    return get_book_metadata(config=config)


def save_subjects(book_id: int, subjects: list[str], config: dict) -> None:
    """Store subjects on the book row."""
    from librarian.db import update_book_fields

    update_book_fields(book_id, config, subjects=subjects)


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
    import argparse

    parser = argparse.ArgumentParser(
        prog="librarian-classify",
        description="Classify books by subject using LLM analysis.",
    )
    parser.add_argument("--auto", action="store_true",
                        help="Accept all LLM suggestions without prompting")
    parser.add_argument("--force", action="store_true",
                        help="Re-classify books that already have subjects")
    parser.add_argument("--book-id", action="append", dest="book_ids",
                        type=int, default=[],
                        help="Only classify specific book IDs (repeatable)")
    return parser.parse_args()


def main():
    """CLI entry point for classification."""
    args = parse_args()

    from librarian.db import list_extracted_book_ids

    config = load_config()
    output_path = expand_path(config["output_path"])
    sample_size = config.get("classification", {}).get("sample_size", 5000)

    books = get_books(config)

    for book_id in list_extracted_book_ids(config):
        book_dir = output_path / str(book_id)

        # Filter by book ID if specified
        if args.book_ids and book_id not in args.book_ids:
            continue

        book_meta = books.get(book_id, {})
        title = book_meta.get("title", "Unknown")
        existing_subjects = book_meta.get("subjects") or []

        # Skip if already classified (unless --force)
        if existing_subjects and not args.force:
            print(f"[{book_id}] {title}: Already classified ({', '.join(existing_subjects)}), skipping")
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
            if not args.auto:
                suggestions = interactive_approve(title, [])
            if not suggestions:
                continue

        # Auto or interactive approval
        if args.auto:
            approved = suggestions
            print(f"[{book_id}] Auto-approved: {approved}")
        else:
            approved = interactive_approve(title, suggestions)

        if approved:
            save_subjects(book_id, approved, config)
            print(f"[{book_id}] Saved subjects: {approved}")
        else:
            print(f"[{book_id}] Skipped")


if __name__ == "__main__":
    main()
