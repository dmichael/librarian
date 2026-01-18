# Session Handoff: Librarian Project

**Date**: 2026-01-17
**Branch**: `feature/makefile-pipeline`

---

## Current Session: Concurrent Extraction/Indexing Fixes

### Status: COMPLETE

Fixed concurrent execution crashes when running `make -j` for parallel extraction and indexing.

### What Was Fixed

**1. Marker/Surya MPS Crash (extract.py)**

Two concurrent `marker_single` processes crash with `IndexError` due to surya library's unsafe MPS/GPU access patterns (global torch._dynamo config, static address registration conflicts).

Fix: File-based lock serializes marker_single calls:
```python
MARKER_LOCK = Path("/tmp/librarian-marker.lock")
with open(MARKER_LOCK, "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    subprocess.run(marker_cmd)
```

**2. Calibre SQLite Contention (extract.py, index.py)**

Concurrent `calibredb list` calls fail when SQLite is locked.

Fix: Retry with exponential backoff:
```python
for attempt in range(max_retries):
    if "Another calibre program" in result.stderr:
        delay = 0.5 * (2 ** attempt)
        time.sleep(delay)
        continue
```

**3. Qdrant Local Single-Client (index.py)**

Qdrant local storage doesn't support concurrent access.

Fix: File-based lock around Qdrant operations:
```python
QDRANT_LOCK = Path("/tmp/librarian-qdrant.lock")
with open(QDRANT_LOCK, "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    _run_indexing(...)
```

**4. Makefile Ingest ID Bug**

`grep -oE '[0-9]+'` matched wrong number. Fixed to grep from "Added book ids:" line.

### Files Modified

```
src/librarian/extract.py   # Marker lock + calibredb retry
src/librarian/index.py     # Qdrant lock + calibredb retry
Makefile                   # clear-lock target, ingest ID fix
```

### Concurrency Model

```
make -j4 all
├── ingest (sequential shell loop)
├── extract (parallel launch → serialized at marker_single via lock)
└── index (parallel launch → serialized at Qdrant via lock)
```

---

## Future: Vector Store Abstraction

**Problem**: Qdrant local mode is single-client, requiring a lock. This serializes indexing.

**Options**:
1. Run Qdrant as server (docker) - true concurrency
2. Switch to LanceDB - native concurrent local access
3. Keep lock - works for now

**Recommended**: Abstract vector store behind config-driven factory:

```yaml
vector_store:
  backend: qdrant  # or lancedb, qdrant-server
  path: ~/data/librarian/vectors
  collection: librarian_full
```

```python
def get_vector_store(config: dict) -> BasePydanticVectorStore:
    backend = config["vector_store"]["backend"]
    if backend == "qdrant":
        return QdrantVectorStore(client=QdrantClient(path=...))
    elif backend == "lancedb":
        return LanceDBVectorStore(uri=...)
    elif backend == "qdrant-server":
        return QdrantVectorStore(client=QdrantClient(host=..., port=...))
```

LlamaIndex already provides the `VectorStore` interface - we just need config-driven initialization.

---

## Previous Session: Marker Progress Fix + Fund Industry Extraction

### Directory Structure Reorganized

Separated upstream staging directories from the main source folder:

```
~/data/librarian/
├── calibre/              # Calibre library (source of truth)
├── converted/            # Extracted markdown by book_id
│   └── {id}/full.md
├── kindle/               # Decrypted KFX files from device (by serial)
│   └── GR73H30154540QV4/
├── kindle-captures/      # Screenshot captures for DRM workaround
│   └── the-fund-industry/
│       ├── page_*.png    # 400 captured screenshots
│       └── the-fund-industry.pdf  # Combined PDF (538MB)
├── qdrant/               # Vector store
└── source/               # Ready-to-ingest files (intake folder)
```

Config updated in `config/settings.yaml`:
```yaml
kindle_serial: GR73H30154540QV4
kindle_source_path: ~/data/librarian/kindle
kindle_captures_path: ~/data/librarian/kindle-captures
```

### DRM Extraction Paths

| DRM Type | Method | Success Rate | Notes |
|----------|--------|--------------|-------|
| **CLIENT_ID** | Device serial + DeDRM | Works | 7/8 books succeeded |
| **ACCOUNT_SECRET** | Screenshot capture | Works | The Fund Industry (this session) |

See `docs/kindle-screenshot-capture.md` for screenshot capture procedure.

### Files Modified

```
src/librarian/extract.py           # Fixed subprocess to preserve tqdm progress
scripts/kindle_screenshot.py       # Updated to use config paths
config/settings.yaml               # Added kindle_source_path, kindle_captures_path
.worktrees/.../kindle_extract.py   # Updated to use config paths
docs/kindle-screenshot-capture.md  # New: screenshot capture procedure
AGENTS.md                          # Added DRM paths reference
```

### When Extraction Completes

1. Verify output: `ls ~/data/librarian/converted/105/`
2. Test RAG query: `librarian-ask "What is a mutual fund?"`

---

## Previous Session: Kindle Screenshot Capture Tool

### What Was Built

**`scripts/kindle_screenshot.py`** - Automated Kindle page capture:
- Uses Quartz framework for precise window capture
- AppleScript for app activation and keyboard page turning
- MD5 hash duplicate detection to auto-stop at end of book
- Metadata JSON output for pipeline integration
- Resume support with `--start-page`

```bash
# Capture pages
python scripts/kindle_screenshot.py --book "Book Name" --pages 400

# Combine to PDF
python scripts/kindle_screenshot.py --book "Book Name" --combine-only
```

### "The Fund Industry" Capture Complete

- 400 pages captured: `page_0001.png` through `page_0400.png`
- Combined PDF: `the-fund-industry.pdf` (538MB)
- Added to Calibre as book ID 105

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          KINDLE EXTRACTION                               │
├─────────────────────────────────────────────────────────────────────────┤
│  Kindle Device ──▶ kindle/{serial}/ ──▶ Calibre ──▶ Extract ──▶ Index   │
│                ^                      (DeDRM)                            │
│                └── Manual copy (OpenMTP/Finder)                          │
│                                                                          │
│  OR (for ACCOUNT_SECRET DRM):                                            │
│                                                                          │
│  Kindle App ──▶ Screenshot ──▶ kindle-captures/ ──▶ PDF ──▶ Calibre     │
│                (automated)      (combined)               ▼               │
│                                                     Extract ──▶ Index    │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                          MAIN PIPELINE                                   │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐  │
│  │   Extract   │──▶│  Classify   │──▶│    Index    │──▶│     Ask     │  │
│  │  (markdown) │   │ (subjects)  │   │  (vectors)  │   │   (RAG)     │  │
│  └─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘  │
│       marker        ollama/claude     BGE + Qdrant     Grounded answers │
│                                                        with citations    │
└─────────────────────────────────────────────────────────────────────────┘
```

### CLI Commands

```bash
librarian-extract                    # PDF/EPUB → markdown (shows progress!)
librarian-classify                   # LLM suggests subjects
librarian-index                      # Embed and store in Qdrant
librarian-ask "question"             # RAG synthesis with citations
librarian-ask --library therapy "q"  # Scoped to library
```

---

## Technical Notes

### Marker OCR Performance

For a 400-page screenshot PDF on M1 Mac:
- Layout Recognition: ~3 seconds/page
- OCR Error Detection: Fast (few seconds total)
- Text Recognition: ~1 second/page
- Total: ~25-30 minutes

### Known Issues

1. **TableRecEncoderDecoderModel warning**: Falls back to CPU from MPS, not critical
2. **Marker progress on stderr**: Works correctly now that subprocess inherits terminal

### Environment

- **Python 3.12** required (3.14 has Pydantic issues with marker-pdf)
- **marker-pdf**: Installed in venv, uses surya for OCR
- **Ollama**: `brew services list | grep ollama`
- **Qdrant**: Local persistence in `~/data/librarian/qdrant/`

---

## Calibre Custom Columns

| Column | Purpose |
|--------|---------|
| `source_hash` | Detect when source file changes |
| `extraction_date` | When extraction ran |
| `extraction_tool` | Tool + version |
| `subjects` | Classification tags |
| `library` | Bounded collection (therapy, investing) |

---

## Open Questions

1. **Annotation capture** - How to integrate reading highlights/notes?
2. **Agent personas** - Build specific agent wrappers (therapy coach, etc.)?
3. **Multi-book reasoning** - Cross-reference across library?

---

## To Resume

1. Check extraction status: `tail -20 /private/tmp/claude/-Users-dmichael-projects-librarian/tasks/b6a5f2b.output`
2. If complete: `librarian-ask "What is a mutual fund?"`
3. Verify: `ls ~/data/librarian/converted/105/`
