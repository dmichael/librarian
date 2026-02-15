# Agent Guide

How to work in this repo — for AI agents and human contributors alike.

## Read first

1. `HANDOFF.md` — architecture notes and key decisions
2. `SPEC.md` — design intent and vision
3. `docs/QUICKSTART.md` — bootstrap and common commands
4. `README.md` — overview and setup

## Project shape

- Single Python package: `src/librarian/`
- Config: `config/settings.yaml` with optional machine override `config/settings.local.yaml`
- Storage paths are outside the repo by default (under `~/data/librarian/`)
- MCP server is the primary interface (port 8811, streamable HTTP)

## Directory structure

```
src/librarian/
  mcp_server.py       MCP tools + HTTP upload endpoint
  db.py               SQLAlchemy models (Book table)
  query.py            Semantic search / retrieval
  index.py            Chunking, embedding, pgvector insertion
  cloud_extract.py    Modal-based PDF/EPUB extraction
  config.py           Configuration loading
  vectorstore/        Pluggable vector store backends

config/
  settings.yaml       Default configuration (generic defaults)
  settings.local.yaml Machine-specific overrides (not committed)
  taxonomy.yaml       Subject taxonomy definitions

~/data/librarian/     (default, outside repo)
  intake/ebooks/      Drop zone for new PDFs/EPUBs
  converted/          Extracted content by book_id
  calibre/            Calibre library (format conversion)
```

## Conventions

- Prefer small, reversible changes. Keep the pipeline working end-to-end.
- Don't commit data under `~/data/librarian/` to git.
- Keep taxonomy changes backwards-compatible (existing subject tags should remain valid).

## MCP tools (primary interface)

| Tool | Description |
|------|-------------|
| `upload_book` | Returns HTTP upload endpoint + curl example |
| `ingest_book` | Register a book by path on the data volume |
| `extract_book` | Extract to markdown via Modal cloud GPU |
| `index_book` | Embed chunks and store in pgvector |
| `search` | Semantic search with subject/library filters |
| `update_book` | Tag with subjects and library (propagates to vectors) |
| `delete_book` | Remove book record and vectors |
| `suggest_tags` | Auto-suggest subjects/library from content |
| `list_books` | List all books with metadata |
| `book_status` | Pipeline statistics |
| `library_profile` | Summary for agent onboarding |

## CLI commands (legacy pipeline)

| Command | Description |
|---------|-------------|
| `librarian-serve` | Run MCP server |
| `librarian-extract` | Extract PDF/EPUB to markdown |
| `librarian-extract --cloud` | Extract on Modal A100s |
| `librarian-index` | Embed and store in vector DB |
| `librarian-query` | Pure retrieval (returns chunks) |
| `librarian-ask` | RAG synthesis with citations |
| `librarian-classify` | LLM-assisted subject classification |
| `librarian-status` | Show pipeline state |

## Running CLI commands

This project uses a Python venv. All librarian commands must be run via the venv:

```bash
# Option 1: Use full path
.venv/bin/librarian-extract --help

# Option 2: Activate venv first
source .venv/bin/activate
librarian-extract --help
```

If commands aren't found, reinstall: `.venv/bin/pip install -e ".[serve]"`

## Where to look for issues

- MCP server / tools: `src/librarian/mcp_server.py`
- Extraction: `src/librarian/extract.py`, `src/librarian/cloud_extract.py`
- Indexing / retrieval: `src/librarian/index.py`, `src/librarian/query.py`
- RAG synthesis: `src/librarian/ask.py`
- Classification: `src/librarian/classify.py` + `config/taxonomy.yaml`
- Vector store backends: `src/librarian/vectorstore/`
- Database models: `src/librarian/db.py`
