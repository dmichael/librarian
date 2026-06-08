# Quickstart

This guide is optimized for “fresh machine / fresh clone” bootstrapping.

Note: this document focuses on local CLI workflow. For current MCP/deployment
workflow and host topology, see `README.md`, `docs/AGENT_CONTEXT.md`, and
`docs/DB_MAINTENANCE.md`.

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

## 4) One file end-to-end (file mode → ask)

File mode is the no-database path: extract a file to an artifacts directory,
then index that directory into the vector store.

1) Extract a file to the configured `output_path` (one `{content_hash}/`
   subdirectory is created per file). PDFs need `LIBRARIAN_SPARK_URL` (and
   `GROBID_BASE_URL` for references) set:

```bash
librarian extract /path/to/book.pdf -o ~/data/librarian/converted
```

2) Index all unindexed extraction directories under `output_path`:

```bash
librarian index            # or: librarian index ~/data/librarian/converted/<hash>
```

3) Ask / query:

```bash
librarian-ask "What are the main ideas in this book?"
librarian-query --subject psychology/* "emotion regulation"
```

## 5) Common operations

- Extract several files: `librarian extract a.pdf b.epub -o DIR`
- Re-index everything: `librarian index --force`
- Scoped RAG: `librarian-ask --library therapy "How do I cope when overwhelmed?"`

Service mode (MCP server + postgres `books` table) is the path agents use; its
tools (`extract_book`, `index_book`, `search`, …) key on integer book ids.
`librarian-classify` and `librarian-enrich` are service-mode utilities — they
read/write the `books` table.

## Data layout (default)

The defaults in `config/settings.yaml` assume:

```
~/data/librarian/
  converted/        # Extracted markdown/JSON artifacts (per content hash or book id)
  intake/ebooks/    # Upload/intake drop zone
```
