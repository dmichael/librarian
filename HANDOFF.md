# Architecture Notes

## Pipeline

```
PDF / EPUB  →  extract (Modal A100)  →  index (BGE + pgvector)  →  search (MCP)
```

All orchestration is via MCP tools over streamable HTTP. The server runs on
port 8811. Agents upload files via `POST /upload`, then call `extract_book`,
`index_book`, and `search` through MCP.

## Key Design Decisions

- **PostgreSQL books table** is source of truth for IDs and pipeline state
  (not Calibre, which is kept only for format conversion)
- **All extraction goes through Modal/marker** — even EPUBs — for consistent
  quality. Native EPUB extraction was unreliable on flat-structure EPUBs.
- **Embedding model (BGE) baked into Docker image** — no runtime download,
  runs on CPU in production
- **Vector store is pgvector** — chunks stored as JSONB metadata + vector(768)
- **Tagging propagates to chunks** — `update_book` pushes library/subjects
  changes to pgvector metadata so search filters work immediately

## Collections

| Collection | Content |
|------------|---------|
| `librarian_full` | Text chunks from all books |
| `librarian_equations` | Extracted LaTeX equations |
| `librarian_chapters` | Chapter-level summaries |

## Known Issues

- **Authors metadata in pgvector** has char-split values for older books
  (books table is correct; chunk metadata needs backfill)
- Python 3.12 required (3.14 has Pydantic issues with marker-pdf)
