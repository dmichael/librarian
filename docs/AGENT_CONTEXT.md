# Agent Context

Operational and architectural context for agents.
This file is intentionally descriptive, not normative.
Behavior and safety rules live in `AGENTS.md`.

## Deployment reality

- Live deployment host: `ms-01.local` (Linux x86 box).
- Live PostgreSQL for Librarian also runs on `ms-01.local`.
- Many agent sessions run on a separate dev machine.
- Confirm host target before running operational commands.

## Project shape

- Single Python package: `src/librarian/`
- Config: `config/settings.yaml` with optional machine override `config/settings.local.yaml`
- Default data root: `~/data/librarian/` (outside repo)
- MCP server is primary interface (streamable HTTP, default port `8811`)

## Directory map

```text
src/librarian/
  mcp_server.py       MCP tools + HTTP upload/download routes
  db.py               SQLAlchemy model + helpers for the books table
  extract.py          Extraction orchestration (Marker + GROBID)
  extractors/         Per-tool extractors (marker, grobid)
  index.py            Chunking, embedding, vector insertion
  query.py            Retrieval logic
  cli/                `librarian` CLI (extract / index / query / serve)
  config.py           Config loading
  vectorstore/        Backend abstraction

config/
  settings.yaml
  settings.local.yaml (optional, uncommitted)
  taxonomy.yaml
```

## Current pgvector collections

- `librarian_full` — text chunks
- `librarian_equations` — extracted equations
- `librarian_chapters` — chapter summaries

## MCP tools (current)

- `upload_book` — returns HTTP upload endpoint + curl example
- `ingest_book` — register source path as a book
- `extract_book` — run extraction
- `index_book` — embed and index
- `search` — semantic search
- `text_search` — exact substring search
- `update_book` — update metadata/tags
- `delete_book` — delete book + vectors
- `book_status` — status of a book
- `list_books` — list books
- `library_profile` — onboarding summary
- `suggest_tags` — heuristic subject/library suggestion
- `verify_book` — post-index QA checks
- `download_book` — get source download URL

## CLI commands

- `librarian` — dispatcher (`extract` / `index` / `query` / `serve`)
- `librarian-serve` — MCP server
- `librarian-extract` / `librarian-index` — file-mode extract / index
- `librarian-query` / `librarian-ask` — retrieval / RAG
- `librarian-classify` / `librarian-enrich` — service-mode (books table) utilities

Run CLI via venv, for example:

```bash
.venv/bin/librarian-index
```

If scripts are missing after environment changes:

```bash
.venv/bin/pip install -e ".[serve]"
```

## Known issues to remember

- Older indexed vectors may contain char-split `authors` metadata.
- Python 3.12 is the supported runtime for extraction/indexing stack.

## Common code entry points

- MCP/server: `src/librarian/mcp_server.py`
- Extraction: `src/librarian/extract.py`, `src/librarian/extractors/`
- Indexing/retrieval: `src/librarian/index.py`, `src/librarian/query.py`
- Classification: `src/librarian/classify.py`
- DB model + helpers: `src/librarian/db.py`
- Vector backends: `src/librarian/vectorstore/`
