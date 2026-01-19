# Spec: Calibre State Unification

## Goal

Unify all pipeline state tracking into Calibre custom columns. Remove `pipeline.json` and `state.py`.

## Current State

- `state.py` / `pipeline.json`: Tracks kindle-extract success/failure by content hash
- `*source_hash` Calibre column: Tracks whether extraction is stale
- `*subjects` Calibre column: Classification results
- Books that fail DRM are currently removed from Calibre (recent change)

## Target State

Calibre is the single source of truth. All books that enter the pipeline are tracked in Calibre, including failures.

## Custom Columns

### `*status` (text)

Pipeline status for this book:

| Value | Meaning |
|-------|---------|
| `drm_failed` | DRM decryption failed, book is not usable |
| `imported` | In Calibre, DRM stripped (if applicable), ready for extraction |
| `extracted` | Marker extraction complete, JSON/markdown available |
| `indexed` | Added to vector store, searchable |

Progression: `drm_failed` is terminal. Normal flow: `imported` → `extracted` → `indexed`

### `*drm_diagnosis` (text, optional)

JSON blob with DRM failure details. Only populated when `*status = drm_failed`.

```json
{
  "drm_type": "ACCOUNT_SECRET",
  "explanation": "Book uses account-bound DRM",
  "action": "Use screenshot capture workflow",
  "timestamp": "2026-01-18T10:00:00"
}
```

### Existing Columns (unchanged)

- `*source_hash`: SHA-256 of source file, for stale detection
- `*subjects`: Classification tags (e.g., "psychology/therapy")

## File Changes

### 1. `kindle_extract.py`

**Remove:** Cleanup of failed DRM books (lines added recently)

**Add:** Set status on success/failure

```python
def add_to_calibre(book_path: Path, library_path: Path) -> tuple[int | None, str | None]:
    # ... existing add logic ...

    if drm_failed:
        # Book is in Calibre but unusable
        # Find the book ID and set status
        after_ids = get_all_book_ids(library_path)
        new_ids = after_ids - before_ids
        if new_ids:
            book_id = new_ids.pop()
            set_calibre_status(book_id, "drm_failed", library_path)
            set_drm_diagnosis(book_id, diagnosis, library_path)
        return None, drm_error

    # Success
    book_id = ...  # existing logic
    set_calibre_status(book_id, "imported", library_path)
    return book_id, None
```

### 2. `extract.py`

**Add:** Set status after marker extraction

```python
def extract_pdf(source: Path, output_dir: Path, book_id: int, library_path: Path) -> bool:
    # ... existing extraction logic ...

    if success:
        set_calibre_status(book_id, "extracted", library_path)
        return True
    return False
```

### 3. `index.py`

**Add:** Set status after indexing

```python
def index_book(book_id: int, ...) -> bool:
    # ... existing indexing logic ...

    if success:
        set_calibre_status(book_id, "indexed", library_path)
        return True
    return False
```

### 4. New: `calibre.py` (helper module)

Centralize Calibre operations:

```python
"""Calibre database operations."""

import subprocess
import json
from pathlib import Path


def set_custom(book_id: int, column: str, value: str, library_path: Path) -> bool:
    """Set a custom column value."""
    cmd = [
        "calibredb", "set_custom",
        "--library-path", str(library_path),
        column, str(book_id), value,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def get_custom(book_id: int, column: str, library_path: Path) -> str | None:
    """Get a custom column value."""
    cmd = [
        "calibredb", "list",
        "--library-path", str(library_path),
        "--fields", f"id,{column}",
        "--for-machine",
        "--search", f"id:{book_id}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    books = json.loads(result.stdout)
    if books:
        return books[0].get(column)
    return None


def set_status(book_id: int, status: str, library_path: Path) -> bool:
    """Set pipeline status for a book."""
    return set_custom(book_id, "status", status, library_path)


def set_drm_diagnosis(book_id: int, diagnosis: dict, library_path: Path) -> bool:
    """Set DRM diagnosis for a failed book."""
    return set_custom(book_id, "drm_diagnosis", json.dumps(diagnosis), library_path)


def get_books_by_status(status: str, library_path: Path) -> list[dict]:
    """Get all books with a given status."""
    cmd = [
        "calibredb", "list",
        "--library-path", str(library_path),
        "--fields", "id,title,authors,*status",
        "--for-machine",
        "--search", f"*status:{status}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return json.loads(result.stdout)
```

### 5. Delete: `state.py`

Remove entirely. All state now in Calibre.

### 6. Update: `config/settings.yaml`

Remove `state_path` - no longer used:

```yaml
# Remove this line:
# state_path: ~/data/librarian/state
```

### 7. Update: `librarian-status` command

Rewrite to query Calibre instead of pipeline.json:

```python
def status_main():
    """Show pipeline status from Calibre."""
    config = load_config()
    library_path = expand_path(config["library_path"])

    # Count by status
    for status in ["drm_failed", "imported", "extracted", "indexed"]:
        books = get_books_by_status(status, library_path)
        print(f"{status}: {len(books)}")

    # Show DRM failures with diagnosis
    if args.failed:
        failed = get_books_by_status("drm_failed", library_path)
        for book in failed:
            print(f"  {book['title']}")
            diagnosis = json.loads(book.get("*drm_diagnosis", "{}"))
            if diagnosis.get("action"):
                print(f"    Action: {diagnosis['action']}")
```

## Migration

### 1. Create custom columns

```bash
calibredb add_custom_column --library-path ~/data/librarian/calibre \
  status "Status" text
calibredb add_custom_column --library-path ~/data/librarian/calibre \
  drm_diagnosis "DRM Diagnosis" text
```

### 2. Backfill existing books

Books currently in Calibre without status:
- If extracted (has `{id}.json` in converted/): set `status = extracted`
- If indexed (in vector store): set `status = indexed`
- Otherwise: set `status = imported`

### 3. Re-import failed DRM books

The 28 books tracked in pipeline.json as failed need to be re-attempted so they enter Calibre with `drm_failed` status.

```bash
# Clear the old state
rm ~/data/librarian/state/pipeline.json

# Re-run kindle-extract (will re-try all source files)
librarian-kindle-extract
```

### 4. Delete old state system

```bash
# Remove state directory entirely
rm -rf ~/data/librarian/state/

# Remove state module
rm src/librarian/state.py

# Remove state_path from config
# (only used by state.py and kindle_extract.py, both being updated)
```

**State directory contents being removed:**
- `pipeline.json` - kindle-extract success/failure tracking
- `ingested/` - marker files for PDF ingestion
- `extracted/`, `failed/`, `indexed/` - unused subdirs
- `extraction.log.archive` - old logs

## Queries

After migration, use Calibre to query pipeline state:

```bash
# All DRM failures
calibredb list --library-path ~/data/librarian/calibre --search "*status:drm_failed"

# Books needing extraction
calibredb list --library-path ~/data/librarian/calibre --search "*status:imported"

# Books needing indexing
calibredb list --library-path ~/data/librarian/calibre --search "*status:extracted"

# Fully processed
calibredb list --library-path ~/data/librarian/calibre --search "*status:indexed"
```

## Verification

```bash
# 1. Check status distribution
librarian-status

# 2. Verify DRM failures are tracked
librarian-status --failed

# 3. Verify extraction pipeline still works
librarian-extract --book-id 153
calibredb list --search "id:153" --fields "*status"  # Should show "extracted"

# 4. Verify indexing updates status
librarian-index --book-id 153
calibredb list --search "id:153" --fields "*status"  # Should show "indexed"
```
