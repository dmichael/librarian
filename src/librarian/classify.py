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
    output_path = expand_path(config["output_path"])
    sample_size = config.get("classification", {}).get("sample_size", 5000)

    books = get_books(config)

    # Find extracted books
    extracted_dirs = [d for d in output_path.iterdir() if d.is_dir() and d.name.isdigit()]

    for book_dir in sorted(extracted_dirs, key=lambda d: int(d.name)):
        book_id = int(book_dir.name)

        # Filter by book ID if specified
        if args["book_ids"] and book_id not in args["book_ids"]:
            continue

        book_meta = books.get(book_id, {})
        title = book_meta.get("title", "Unknown")
        existing_subjects = book_meta.get("subjects") or []

        # Skip if already classified (unless --force)
        if existing_subjects and not args["force"]:
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
            save_subjects(book_id, approved, config)
            print(f"[{book_id}] Saved subjects: {approved}")
        else:
            print(f"[{book_id}] Skipped")


if __name__ == "__main__":
    main()
