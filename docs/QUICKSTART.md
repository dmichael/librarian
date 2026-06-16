# Quickstart

This guide is optimized for “fresh machine / fresh clone” bootstrapping.

Note: for MCP/deployment workflow and host topology, see `README.md`,
`docs/AGENT_CONTEXT.md`, and `docs/DB_MAINTENANCE.md`.

## 1) Prerequisites

- Python 3.12+
- For PDF extraction: a Marker HTTP service reachable at `LIBRARIAN_SPARK_URL`,
  and (for references/citations/sections) a GROBID service at `GROBID_BASE_URL`
- A vector store (file-based Qdrant for local use, or pgvector for the service)
- Optional: Ollama running locally for classification and RAG synthesis

Calibre is no longer used.

## 2) Python environment

From the repo root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## 3) Configure local paths (recommended)

Create `config/settings.local.yaml` (it overrides `config/settings.yaml`):

```bash
cp config/settings.local.example.yaml config/settings.local.yaml
```

Then edit paths/models as needed.

## 4) One file end-to-end (MCP tools → search)

The MCP server is the pipeline; its tools key on integer book ids. PDFs need
`LIBRARIAN_SPARK_URL` (and `GROBID_BASE_URL` for references) set.

1) `ingest_book` (or `upload_book` over HTTP) — registers the book and
   returns its id.
2) `extract_book(book_id)` — runs the extractors in the background; poll
   `book_status(book_id)`.
3) `index_book(book_id)` — embeds into the vector store.
4) Search via the `search` / `text_search` MCP tools, or from a shell:

```bash
librarian-query "What are the main ideas in this book?"
librarian-query --subject psychology/* "emotion regulation"
```

## 5) Common operations

- Re-extract / re-index a book: `extract_book(book_id, force=True)`, then
  `index_book(book_id, force=True)`
- Scoped query: `librarian-query --library therapy "How do I cope when overwhelmed?"`

`librarian-classify` and `librarian-enrich` are maintenance utilities — they
read/write the `books` table.

## Data layout (default)

The defaults in `config/settings.yaml` assume:

```
~/data/librarian/
  converted/        # Extracted markdown/JSON artifacts (per content hash or book id)
  intake/ebooks/    # Upload/intake drop zone
```
