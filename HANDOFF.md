# Session Handoff: Librarian Project

**Date**: 2025-01-15
**Branch**: `claude/librarian-knowledge-base-4uRmW`

---

## Session 2025-01-15: Full Pipeline Implemented

### What Was Built

Complete RAG pipeline from book to grounded answers:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Extract   │────▶│  Classify   │────▶│    Index    │────▶│     Ask     │
│  (markdown) │     │ (subjects)  │     │  (vectors)  │     │   (RAG)     │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
     marker           ollama/claude       BGE + Qdrant       Grounded answers
                                                             with citations
```

### CLI Commands

```bash
librarian-extract                    # PDF/EPUB → markdown
librarian-classify                   # LLM suggests subjects, human approves
librarian-classify --auto            # Auto-accept LLM suggestions
librarian-index                      # Embed and store in Qdrant
librarian-query "question"           # Pure retrieval (returns chunks)
librarian-ask "question"             # RAG synthesis with citations
librarian-ask --library therapy "q"  # Scoped to library
```

### Key Features

| Feature | Implementation |
|---------|----------------|
| **Extraction** | Marker for PDF, custom EPUB parser |
| **Classification** | Ollama (llama3.2) or Claude API |
| **Embeddings** | BGE-base-en-v1.5 (local, Apple Silicon) |
| **Vector Store** | Qdrant (local persistence) |
| **Page Numbers** | Extracted from Marker's embedded markers |
| **Library Fences** | `--library` flag scopes queries |
| **Subject Filters** | `--subject psychology/*` for faceted search |
| **RAG Synthesis** | Ollama/Claude with grounded citations |

### Calibre Custom Columns

| Column | Purpose |
|--------|---------|
| `source_hash` | Detect when source file changes |
| `extraction_date` | When extraction ran |
| `extraction_tool` | Tool + version |
| `subjects` | Classification tags (e.g., "psychology/therapy") |
| `library` | Bounded collection (e.g., "therapy", "investing") |

### Data Locations

```
~/data/librarian/
├── calibre/           # Calibre library (source of truth)
├── converted/         # Extracted markdown by book_id
│   └── {id}/full.md
├── qdrant/            # Vector store
└── source/            # Intake folder for new books
```

### Example Usage

```bash
# Full pipeline for new book
calibredb add ~/Downloads/book.pdf --library-path ~/data/librarian/calibre
librarian-extract --book-id 3
librarian-classify --book-id 3
librarian-index --book-id 3

# Ask questions with citations
librarian-ask --library therapy "How do I cope when overwhelmed?"
# Returns synthesized answer with page-numbered citations
```

---

## Resolved Questions

| Question | Resolution |
|----------|------------|
| Embedding model | BGE-base-en-v1.5 (local, good quality/speed balance) |
| Vector store | Qdrant (local, supports filtering) |
| Chunking | 512 tokens, 50 overlap, sentence-aware |
| LLM for classification | Ollama llama3.2 (local) or Claude (API) |
| Library boundaries | `library` custom column in Calibre, filterable at query time |

---

## Open Questions

1. **Annotation capture** - How to integrate reading highlights/notes?
2. **Agent personas** - Build specific agent wrappers (therapy coach, etc.)?
3. **Multi-book reasoning** - Cross-reference across library?
4. **Caching layer** - Save embeddings to parquet for portability?

---

## Files Created This Session

```
src/librarian/
├── ask.py         # RAG synthesis with citations
├── classify.py    # LLM-assisted classification
├── index.py       # Embedding + Qdrant storage
├── query.py       # Pure retrieval with filters

config/
├── settings.yaml  # Full config with embedding/classification settings
└── taxonomy.yaml  # Subject hierarchy

docs/
└── x402-extraction-service.md  # Spec for pay-per-page extraction API
```

---

## Technical Notes

- **Python 3.12** required (3.14 has Pydantic issues with marker-pdf)
- **Ollama** installed via `brew install ollama`, running as service
- **Apple Silicon** uses MPS for embeddings (~50 embeddings/sec)
- **Page numbers** extracted from Marker's `<span id="page-XXX">` markers

---

## To Resume

1. Read this file for context
2. Run `librarian-ask --library therapy "test query"` to verify setup
3. Check Ollama: `brew services list | grep ollama`
4. Add more books to grow the library

---

## Previous Session Context

See git history for earlier HANDOFF.md versions covering:
- Calibre-centric architecture decisions
- Initial spec and vision discussions
- Tool evaluation and selection rationale
