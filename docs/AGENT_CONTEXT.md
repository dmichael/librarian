# Agent Context

Operational and architectural context for agents.
This file is intentionally descriptive, not normative.
Behavior and safety rules live in `AGENTS.md`.

## Deployment reality

- Live deployment host: `agents.local` (Mac mini).
- Live PostgreSQL for Librarian also runs on `agents.local`.
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
  db.py               SQLAlchemy model for books table
  query.py            Retrieval logic
  index.py            Chunking, embedding, vector insertion
  cloud_extract.py    Modal extraction
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

## Legacy CLI commands

- `librarian-serve`
- `librarian-extract` / `librarian-extract --cloud`
- `librarian-index`
- `librarian-query`
- `librarian-ask`
- `librarian-classify`
- `librarian-status`

Run CLI via venv, for example:

```bash
.venv/bin/librarian-status
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
- Extraction: `src/librarian/extract.py`, `src/librarian/cloud_extract.py`
- Indexing/retrieval: `src/librarian/index.py`, `src/librarian/query.py`
- Classification: `src/librarian/classify.py`
- DB model: `src/librarian/db.py`
- Vector backends: `src/librarian/vectorstore/`
