# Spec: Librarian Pipeline Cleanup

**Date**: 2026-01-17
**Status**: Draft

---

## Problem Statement

The librarian project has grown organically without clear boundaries between pipeline stages. This causes:

1. **Mixed directories**: `source/` serves as intake, staging, AND output
2. **ID collisions**: Kindle extraction searches by first word of title, returns wrong Calibre IDs
3. **No idempotency**: No tracking of processed vs pending files
4. **Silent failures**: DRM errors say "failed" without diagnosing why
5. **CLI/Skill confusion**: Interactive workflows (screenshot capture) mixed with batch CLI commands

---

## Current State

### Directory Structure (Problematic)

```
~/data/librarian/
├── calibre/              # Source of truth (OK)
├── converted/            # Extracted markdown by book_id (OK)
├── kindle/               # Raw device files by serial (OK)
├── kindle-captures/      # Screenshot captures (OK)
├── qdrant/               # Vector store (OK)
└── source/               # PROBLEM: Mixed purposes
    ├── *.epub            # Output from kindle-extract (should be staging)
    ├── *.pdf             # Manual uploads (intake)
    ├── kindle/           # Old nested structure
    └── _kindle-failed/   # Failed DRM (debugging artifacts)
```

### CLI Commands (8 total)

| Command | Purpose | Issues |
|---------|---------|--------|
| `librarian-ingest` | Add books to Calibre | No ID returned |
| `librarian-kindle-sync` | Check sync folder | OK |
| `librarian-extract` | Extract to markdown | OK |
| `librarian-classify` | LLM subject tagging | OK |
| `librarian-index` | Embed to vector store | OK |
| `librarian-query` | Semantic search | OK |
| `librarian-ask` | RAG synthesis | OK |
| `librarian-kindle-extract` | DRM removal | ID collision bug |

### ID Collision Bug

In `kindle_extract.py:77-87`:
```python
# Fallback searches by FIRST WORD only
first_word = title_part.split("_")[0].split()[0]
new_ids = search_calibre(f'title:"{first_word}"', library_path)
return max(new_ids), None  # WRONG: "Mastering Bitcoin" matches "Mastering Ethereum"
```

---

## Proposed Architecture

### 1. Clear Directory Structure

```
~/data/librarian/
├── calibre/                    # Calibre library (source of truth)
│
├── intake/                     # NEW: Drop zone for new files
│   ├── ebooks/                 # PDFs, EPUBs to ingest
│   └── kindle/                 # Synced from device via OpenMTP
│       └── {serial}/
│
├── staging/                    # NEW: Processing workspace
│   ├── drm-stripped/           # DRM-free, ready for Calibre
│   └── drm-failed/             # Failed with diagnosis
│       └── {filename}.diagnosis.json
│
├── converted/                  # Extracted markdown (unchanged)
│   └── {calibre_id}/
│
├── qdrant/                     # Vector store (unchanged)
│
└── state/                      # NEW: Pipeline state tracking
    └── pipeline.json           # What's been processed
```

### 2. Fix ID Collision

Replace fallback title search with before/after ID comparison:

```python
def add_to_calibre_safe(book_path: Path, library_path: Path):
    # 1. Snapshot IDs before
    before_ids = set(get_all_book_ids(library_path))

    # 2. Add the book
    result = run_calibredb(["add", str(book_path)], library_path)

    # 3. Try parsing from output (reliable when present)
    match = re.search(r"Added book ids?: (\d+)", result.stdout)
    if match:
        return int(match.group(1)), None

    # 4. Compare before/after (fallback)
    after_ids = set(get_all_book_ids(library_path))
    new_ids = after_ids - before_ids

    if len(new_ids) == 1:
        return new_ids.pop(), None

    # 5. Fail explicitly, don't guess
    return None, "Could not determine assigned ID"
```

### 3. State Tracking for Idempotency

```python
# src/librarian/state.py

class PipelineState:
    """Track processed files by content hash."""

    def __init__(self, state_dir: Path):
        self.state_file = state_dir / "pipeline.json"
        self.state = self._load()

    def is_processed(self, file_path: Path) -> bool:
        file_hash = compute_hash(file_path)
        return file_hash in self.state.get("processed", {})

    def record_success(self, file_path: Path, calibre_id: int):
        file_hash = compute_hash(file_path)
        self.state.setdefault("processed", {})[file_hash] = {
            "calibre_id": calibre_id,
            "timestamp": datetime.now().isoformat(),
            "source": str(file_path),
        }
        self._save()

    def record_failure(self, file_path: Path, reason: str, diagnosis: dict):
        file_hash = compute_hash(file_path)
        self.state.setdefault("failed", {})[file_hash] = {
            "reason": reason,
            "diagnosis": diagnosis,
            "timestamp": datetime.now().isoformat(),
        }
        self._save()
```

### 4. DRM Diagnostics

Parse DeDRM output to provide actionable diagnosis:

```python
# src/librarian/drm_diagnosis.py

def diagnose_drm_failure(calibre_output: str) -> dict:
    """Analyze DRM failure and return structured diagnosis."""

    output_lower = calibre_output.lower()

    # Check for key patterns
    has_serial = "found 1 keys to try" in output_lower
    wrong_key = "incorrect padding - wrong key" in output_lower
    no_mac_keys = "no k4mac kindle-info" in output_lower

    if has_serial and wrong_key and no_mac_keys:
        return {
            "drm_type": "ACCOUNT_SECRET",
            "serial_configured": True,
            "decryptable": False,
            "action": "Use screenshot capture workflow (see docs/kindle-screenshot-capture.md)",
            "explanation": "Book uses account-bound DRM. Device serial alone cannot decrypt."
        }

    if not has_serial:
        return {
            "drm_type": "UNKNOWN",
            "serial_configured": False,
            "decryptable": False,
            "action": "Configure kindle_serial in config/settings.yaml",
            "explanation": "No decryption keys available."
        }

    return {
        "drm_type": "UNKNOWN",
        "raw_output": calibre_output[:1000]
    }
```

### 5. CLI vs Skills

**CLI Commands** (batch, automated):
- `librarian-ingest` - Add files to Calibre
- `librarian-kindle-extract` - DRM removal
- `librarian-extract` - Markdown extraction
- `librarian-classify --auto` - Auto-classify
- `librarian-index` - Vector indexing
- `librarian-query` / `librarian-ask` - Retrieval

**NEW CLI Commands**:
- `librarian-status` - Show pipeline state dashboard
- `librarian-retry-failed` - Retry failed with updated config

**Skills** (interactive, require user presence):
- `/kindle-capture` - Screenshot capture workflow (move from scripts/)
- `/classify` - Interactive classification approval
- `/diagnose-drm` - Interactive DRM troubleshooting

---

## Implementation Plan

### Phase 1: Directory Structure
1. Create new directories (`intake/`, `staging/`, `state/`)
2. Update `config/settings.yaml` with new paths
3. Add backward compatibility for `source_paths`
4. Update AGENTS.md with new structure

### Phase 2: State Tracking
1. Create `src/librarian/state.py`
2. Integrate with `kindle_extract.py`
3. Add `librarian-status` command

### Phase 3: Fix ID Collision
1. Implement `add_to_calibre_safe()` in `kindle_extract.py`
2. Remove dangerous title-search fallback
3. Test with parallel additions

### Phase 4: DRM Diagnostics
1. Create `src/librarian/drm_diagnosis.py`
2. Integrate with kindle_extract failure handling
3. Write diagnosis JSON to `staging/drm-failed/`

### Phase 5: Skills Migration
1. Create skill manifest for `/kindle-capture`
2. Move `scripts/kindle_screenshot.py` logic to skill
3. Document skill usage

---

## Config Changes

```yaml
# config/settings.yaml additions

# New clear directory structure
intake_path: ~/data/librarian/intake/ebooks
kindle_intake_path: ~/data/librarian/intake/kindle
staging_path: ~/data/librarian/staging
state_path: ~/data/librarian/state

# Keep for backward compat (deprecated)
source_paths:
  - ~/data/librarian/source
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/librarian/kindle_extract.py` | Fix ID collision, add state tracking, DRM diagnosis |
| `src/librarian/config.py` | New path config loading |
| `src/librarian/state.py` | NEW: Pipeline state tracking |
| `src/librarian/drm_diagnosis.py` | NEW: DRM failure analysis |
| `config/settings.yaml` | New directory paths |
| `pyproject.toml` | Add `librarian-status` entry point |

---

## Success Criteria

1. Running `librarian-kindle-extract` twice produces same result (idempotent)
2. No ID collisions even with similar book titles
3. Failed DRM extractions include actionable diagnosis
4. Clear separation: `intake/` for new files, `staging/` for processing, `calibre/` for normalized
5. `librarian-status` shows what's pending/processed/failed

---

## Migration Notes

- Keep `source/` working during transition (symlinks if needed)
- Backfill `state/pipeline.json` from existing `extraction.log`
- No data loss - reorganize, don't delete
