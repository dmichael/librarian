"""External metadata lookup services for ISBN cross-referencing."""

from dataclasses import dataclass

import httpx


@dataclass
class BookMetadata:
    """Metadata retrieved from an external source."""

    title: str | None
    authors: list[str]
    publisher: str | None
    isbn: str | None
    confidence: float  # 1.0 for ISBN match, lower for fuzzy
    source: str  # "google_books", "openlibrary", etc.


def lookup_isbn_google(isbn: str) -> BookMetadata | None:
    """Query Google Books API for ISBN.

    Args:
        isbn: ISBN-10 or ISBN-13 (with or without dashes)

    Returns:
        BookMetadata if found, None otherwise
    """
    # Clean ISBN - remove dashes and spaces
    clean_isbn = isbn.replace("-", "").replace(" ", "")

    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{clean_isbn}"

    try:
        response = httpx.get(url, timeout=10.0)
        if response.status_code != 200:
            return None

        data = response.json()
        if data.get("totalItems", 0) == 0:
            return None

        info = data["items"][0]["volumeInfo"]
        return BookMetadata(
            title=info.get("title"),
            authors=info.get("authors", []),
            publisher=info.get("publisher"),
            isbn=clean_isbn,
            confidence=1.0,
            source="google_books",
        )
    except (httpx.RequestError, httpx.TimeoutException, KeyError, IndexError):
        return None


def lookup_isbn_openlibrary(isbn: str) -> BookMetadata | None:
    """Query OpenLibrary API for ISBN (fallback source).

    Args:
        isbn: ISBN-10 or ISBN-13 (with or without dashes)

    Returns:
        BookMetadata if found, None otherwise
    """
    clean_isbn = isbn.replace("-", "").replace(" ", "")
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{clean_isbn}&format=json&jscmd=data"

    try:
        response = httpx.get(url, timeout=10.0)
        if response.status_code != 200:
            return None

        data = response.json()
        key = f"ISBN:{clean_isbn}"
        if key not in data:
            return None

        info = data[key]
        authors = [a.get("name", "") for a in info.get("authors", [])]

        # OpenLibrary may have multiple publishers
        publishers = info.get("publishers", [])
        publisher = publishers[0].get("name") if publishers else None

        return BookMetadata(
            title=info.get("title"),
            authors=authors,
            publisher=publisher,
            isbn=clean_isbn,
            confidence=1.0,
            source="openlibrary",
        )
    except (httpx.RequestError, httpx.TimeoutException, KeyError, IndexError):
        return None


def lookup_isbn(isbn: str) -> BookMetadata | None:
    """Try multiple sources for ISBN lookup.

    Args:
        isbn: ISBN-10 or ISBN-13 (with or without dashes)

    Returns:
        BookMetadata from first successful source, None if all fail
    """
    # Try Google Books first (generally best results)
    result = lookup_isbn_google(isbn)
    if result:
        return result

    # Fall back to OpenLibrary
    result = lookup_isbn_openlibrary(isbn)
    if result:
        return result

    return None


def normalize_author_name(name: str) -> str:
    """Normalize author name for comparison.

    Handles variations like:
    - "Robert Pozen" vs "Pozen, Robert"
    - "J. M. Selig" vs "J.M. Selig"
    """
    # Remove extra spaces and periods
    normalized = name.lower().strip()
    normalized = " ".join(normalized.split())  # collapse whitespace

    # Handle "Last, First" format
    if ", " in normalized:
        parts = normalized.split(", ")
        if len(parts) == 2:
            normalized = f"{parts[1]} {parts[0]}"

    # Normalize initials: "j.m." -> "j m", "j. m." -> "j m"
    normalized = normalized.replace(".", " ")
    normalized = " ".join(normalized.split())

    return normalized


def compare_authors(existing_authors: list[str], external_authors: list[str]) -> bool:
    """Compare author lists, handling name variations.

    Returns True if they match (same authors), False if different.
    """
    if not existing_authors and not external_authors:
        return True
    if not existing_authors or not external_authors:
        return False

    existing_normalized = {normalize_author_name(a) for a in existing_authors}
    external_normalized = {normalize_author_name(a) for a in external_authors}

    return existing_normalized == external_normalized
