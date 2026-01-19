# Quickstart

This guide is optimized for “fresh machine / fresh clone” bootstrapping.

## 1) Prerequisites

- Python 3.12+
- Calibre installed (provides `calibredb` and `ebook-convert`)
- `marker-pdf` installed (provides `marker_single`) for PDF → Markdown
- Optional: Ollama running locally for classification and RAG synthesis

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

## 4) One book end-to-end (Calibre → ask)

1) Add a book into Calibre (use your real Calibre library path):

```bash
calibredb add /path/to/book.pdf --library-path ~/data/librarian/calibre
```

2) Extract to markdown:

```bash
librarian-extract --book-id 1
```

3) Classify (interactive):

```bash
librarian-classify --book-id 1
```

4) Index:

```bash
librarian-index --book-id 1
```

5) Ask:

```bash
librarian-ask "What are the main ideas in this book?"
```

## 5) Common operations

- Re-extract: `librarian-extract --force --book-id N`
- Re-classify: `librarian-classify --force --book-id N`
- Re-index: `librarian-index --force --book-id N`
- Retrieval only: `librarian-query --subject psychology/* "emotion regulation"`
- Scoped RAG: `librarian-ask --library therapy "How do I cope when overwhelmed?"`

## Data layout (default)

The defaults in `config/settings.yaml` assume:

```
~/data/librarian/
  calibre/      # Calibre library (source of truth)
  converted/    # Extracted markdown by book id
  qdrant/       # Local Qdrant persistence
  source/       # Intake folders (e.g. Kindle sync)
```

