# Spec: Metadata Enrichment from Converted Content

**Date**: 2025-01-18
**Status**: Draft

---

## Problem Statement

When PDFs are ingested into Calibre, metadata extraction often fails:
- Title defaults to filename
- Author shows as "Unknown"
- Publisher, year, ISBN not captured

However, after marker-pdf conversion, we have the actual text content including title pages and copyright info. This metadata exists — it's just not in Calibre.

**Impact**:
- `librarian-read --list` shows "Unknown" for authors
- Browsing Calibre library is unhelpful
- Hard to identify books for re-conversion or debugging

---

## When This Runs

This is a **post-conversion** enrichment step:

```
PDF in Calibre → marker-pdf extract → full.json exists → ENRICHMENT → Calibre updated
```

**Trigger conditions**:
- Book has been converted (full.json exists)
- Calibre metadata is incomplete (author = "Unknown" or title looks like filename)

**Use cases**:
1. After initial conversion completes
2. Batch backfill for existing converted books
3. Before deciding on re-conversion (know what you have)

---

## Approach

### 1. Extract metadata from converted content

Read the first few pages of `full.json` and look for:

| Field | Where to find | Pattern |
|-------|---------------|---------|
| Title | Title page, usually page 1-3 | Largest text block, or block before author |
| Author | Title page, near title | "by {Author}" or line after title |
| Publisher | Copyright page | Known publisher names, "Published by" |
| Year | Copyright page | "© YYYY", "Copyright YYYY", or 4-digit year |
| ISBN | Copyright page | ISBN-10 or ISBN-13 pattern |

### 2. Validate/confirm extraction

For batch mode: auto-apply if confidence is high
For interactive mode: show extracted values for confirmation

### 3. Update Calibre

```bash
calibredb set_metadata {book_id} \
  --field title:"Extracted Title" \
  --field authors:"Extracted Author" \
  --field publisher:"Publisher Name" \
  --field pubdate:"2020-01-01" \
  --field isbn:"978-..." \
  --library-path ~/data/librarian/calibre
```

---

## Implementation

### New file: `src/librarian/enrich.py`

```python
"""Metadata enrichment from converted content."""

def extract_metadata_from_json(book_id: int, config: dict = None) -> dict:
    """Extract metadata from converted full.json.

    Returns:
        {
            "title": str | None,
            "authors": str | None,
            "publisher": str | None,
            "year": int | None,
            "isbn": str | None,
            "confidence": float,  # 0-1
            "source_pages": [int, ...],  # pages metadata was found on
        }
    """
    # Load first 5-10 pages of full.json
    # Look for title page patterns
    # Look for copyright page patterns
    # Return extracted metadata with confidence score
    ...

def get_calibre_metadata(book_id: int, config: dict = None) -> dict:
    """Get current Calibre metadata for comparison."""
    ...

def needs_enrichment(book_id: int, config: dict = None) -> bool:
    """Check if book needs metadata enrichment."""
    meta = get_calibre_metadata(book_id, config)
    # Needs enrichment if:
    # - author is "Unknown"
    # - title looks like a filename (has underscores, .pdf, etc.)
    # - publisher is empty
    ...

def apply_metadata(book_id: int, metadata: dict, config: dict = None) -> bool:
    """Update Calibre with extracted metadata."""
    # Call calibredb set_metadata
    ...

def enrich_book(book_id: int, config: dict = None, dry_run: bool = False) -> dict:
    """Full enrichment flow for one book.

    Returns:
        {
            "book_id": int,
            "status": "enriched" | "skipped" | "failed",
            "before": {...},
            "after": {...},
            "changes": [...],
        }
    """
    ...

def enrich_all(config: dict = None, dry_run: bool = False) -> list[dict]:
    """Enrich all books that need it."""
    ...
```

### CLI: `librarian-enrich`

```bash
# Enrich a specific book
librarian-enrich --book-id 156

# Enrich all books needing it
librarian-enrich --all

# Dry run (show what would change)
librarian-enrich --all --dry-run

# Force re-extraction even if metadata exists
librarian-enrich --book-id 156 --force
```

---

## Extraction Heuristics

### Title Page Detection

1. Find pages 1-5 in the JSON
2. Look for blocks with:
   - `block_type: "SectionHeader"` that's short (< 100 chars)
   - Large text (if font size available)
   - Text before "by" or author-like patterns

### Author Detection

1. Look for "by {Name}" pattern
2. Look for name-like text after title
3. Check copyright page for author attribution

### Copyright Page Detection

1. Look for "©" or "Copyright"
2. Look for "Published by"
3. Look for ISBN patterns: `ISBN[-: ]?(1[03])?[-: ]?[\d-]+`
4. Look for 4-digit years in range 1900-2030

### Confidence Scoring

```python
confidence = 0.0
if title_found_on_title_page: confidence += 0.3
if author_found_near_title: confidence += 0.3
if copyright_page_found: confidence += 0.2
if isbn_found: confidence += 0.2
```

Only auto-apply if confidence >= 0.6

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/librarian/enrich.py` | **NEW** | Metadata extraction and enrichment |
| `pyproject.toml` | **MODIFY** | Add `librarian-enrich` entry point |

---

## Success Criteria

1. `librarian-enrich --book-id 156` extracts and updates metadata
2. `librarian-enrich --all --dry-run` shows what would be updated
3. `librarian-read --list` shows proper titles/authors after enrichment
4. No false positives (wrong metadata applied)
5. Idempotent (running twice doesn't break anything)

---

## Future Extensions

- **LLM-assisted extraction**: Use local LLM to parse unstructured title pages
- **ISBN lookup**: Query OpenLibrary/Google Books API for additional metadata
- **Cover extraction**: Extract cover image from PDF for Calibre
- **Integration with ingest**: Auto-enrich after conversion completes
