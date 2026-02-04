---
name: librarian
description: Query and navigate a personal book library indexed as vector embeddings. Use for knowledge lookups, book reading, pipeline status, and domain-specific Q&A with citations.
metadata:
  {
    "openclaw":
      {
        "emoji": "📚",
        "requires": { "bins": ["librarian-ask"] },
      },
  }
---

# Librarian — Personal Knowledge Base

Query a personal library of books indexed into a PostgreSQL vector store (pgvector). Returns grounded answers with citations and page references.

## When to use this skill

- Answering questions that might be covered by indexed books
- Looking up specific topics, concepts, or techniques
- Navigating book structure (chapters, table of contents)
- Reading specific pages or passages
- Checking what's indexed and pipeline status

## Quick reference

All commands use the venv at `~/projects/librarian/.venv/bin/`.

### Ask a question (RAG with citations)

```bash
~/projects/librarian/.venv/bin/librarian-ask "What is value investing?"
```

Auto-detects query mode (structural, content, or hybrid). Returns a synthesized answer with source references including page numbers.

**Options:**
- `--library NAME` — restrict to a specific library/domain
- `--subject SUBJ` — filter by subject tag
- `--book-id ID` — restrict to a specific book
- `--structural` — force chapter-level retrieval (for "which chapter covers X" questions)
- `--mode MODE` — force mode: `structural`, `content`, or `hybrid`

**Examples:**
```bash
librarian-ask "How do I cope when overwhelmed?" --library therapy
librarian-ask --structural "which chapter covers subadvisories"
librarian-ask --book-id 32 "where is hedge fund regulation first discussed"
```

### Search (raw vector results, no synthesis)

```bash
~/projects/librarian/.venv/bin/librarian-query "wallet encryption"
```

Returns raw matching chunks without LLM synthesis. Useful for browsing.

**Options:**
- `--library TYPE` — restrict to a library
- `--subject SUBJ` — filter by subject (repeatable)
- `--block-type TYPE` — filter by content type: `Text`, `Code`, `SectionHeader`, `Equation`, `Table`, `TableOfContents`, `ListGroup`, `Figure`, `Caption`

### Read book content

```bash
~/projects/librarian/.venv/bin/librarian-read --list                    # List all indexed books
~/projects/librarian/.venv/bin/librarian-read --book-id 156 --structure  # Table of contents
~/projects/librarian/.venv/bin/librarian-read --book-id 156 --page 107   # Read a page
~/projects/librarian/.venv/bin/librarian-read --book-id 156 --pages 105-110  # Page range
~/projects/librarian/.venv/bin/librarian-read --book-id 156 --chapter 3  # Read chapter
~/projects/librarian/.venv/bin/librarian-read --book-id 156 --search "Jacobian"  # Search within book
~/projects/librarian/.venv/bin/librarian-read --book-id 156 --first 5    # First 5 pages
~/projects/librarian/.venv/bin/librarian-read --book-id 156 --references # Citations/bibliography
```

### Pipeline status

```bash
~/projects/librarian/.venv/bin/librarian-status            # Overview
~/projects/librarian/.venv/bin/librarian-status --pending   # Books needing processing
~/projects/librarian/.venv/bin/librarian-status --failed    # DRM failures
~/projects/librarian/.venv/bin/librarian-status --json      # Machine-readable
```

### Statistics

```bash
~/projects/librarian/.venv/bin/librarian-stats             # Vector store stats
~/projects/librarian/.venv/bin/librarian-stats --blocks     # Block type distribution
~/projects/librarian/.venv/bin/librarian-stats --json       # JSON output
```

### Classification

```bash
~/projects/librarian/.venv/bin/librarian-classify           # Interactive classification
~/projects/librarian/.venv/bin/librarian-classify --auto     # Auto-accept LLM suggestions
~/projects/librarian/.venv/bin/librarian-classify --book-id 32  # Classify specific book
```

## Pipeline commands (use with caution)

These modify data. Only run when explicitly asked.

- `librarian-ingest` — add new books from intake directory
- `librarian-extract` — extract PDF/EPUB to markdown (slow locally)
- `librarian-extract-cloud` — extract via Modal A100s (fast, requires Modal account)
- `librarian-index` — embed and store in vector DB
- `librarian-kindle-extract` — DRM removal for Kindle books
- `librarian-enrich` — enrich metadata
- `librarian-validate` — validate metadata
- `librarian-pipeline` — run full pipeline

## Architecture notes

- **Vector store:** PostgreSQL with pgvector extension (768-dim BGE embeddings)
- **Database:** `librarian` on localhost:5432
- **Read-only access:** use Postgres role `wren-ro` for queries
- **Data directory:** `~/data/librarian/`
- **Calibre** is the source of truth for book metadata
- **Ollama** (llama3.2) handles classification and RAG synthesis

## Keeping this skill current

When CLI commands change (new flags, new commands), update this file.
Check: compare `pyproject.toml [project.scripts]` entries against this doc.
