# Librarian

Personal knowledge base for agent intelligence: ingest books into Calibre, extract to Markdown, classify into a subject taxonomy, index into a local vector store (Qdrant), and query/ask with grounded citations.

If you’re new to the repo, start with:
- `HANDOFF.md` (what’s currently in progress / known issues)
- `SPEC.md` (architecture + design intent)
- `docs/QUICKSTART.md` (bootstrap + common workflows)

## What’s here

- `src/librarian/`: CLI entrypoints (`librarian-*`) for ingest/extract/classify/index/query/ask
- `config/settings.yaml`: default config (paths, embedding model/device, vector store, LLM provider)
- `config/taxonomy.yaml`: subject taxonomy used for classification
- `tools/plugins/`: Calibre plugins (DeDRM, KFX Input, etc.)
- `books/`: example extracted markdown (checked-in samples)
- `calibre/`: small checked-in Calibre library (useful for development)

## Core workflow (happy path)

1. Ingest: `librarian-ingest`
2. Extract: `librarian-extract`
3. Classify: `librarian-classify`
4. Index: `librarian-index`
5. Ask: `librarian-ask "your question"`

## Configuration

`src/librarian/config.py` loads `config/settings.local.yaml` if present, otherwise `config/settings.yaml`.

Create a machine-specific config override by copying:
- `config/settings.local.example.yaml` → `config/settings.local.yaml`

## Development

See `AGENTS.md` for agent-oriented conventions and a “doctor checklist” for verifying dependencies.

Optional (Anthropic/Claude API support):

```bash
pip install -e ".[anthropic]"
```
