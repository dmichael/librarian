# Librarian

Give development agents primary, searchable, verifiable reference points.

Librarian registers source artifacts ("books"), ingests PDFs and EPUBs,
extracts them to structured markdown using cloud GPUs, embeds the content into
pgvector, and exposes everything through an MCP server that agents can query
and verify against during development.

## How it works

```
  PDF / EPUB
      │
      ▼
  POST /upload          ← curl a file to the HTTP endpoint
      │
      ▼
  extract_book()        ← runs marker on a Modal A100
      │
      ▼
  index_book()          ← chunks, embeds (BGE), stores in pgvector
      │
      ▼
  search()              ← semantic search filtered by subject or library
```

The entire pipeline is driven through MCP tools. Connect any MCP-compatible
agent (Claude Code, Claude Desktop, custom agents) and it can upload books,
run the pipeline, tag content, and search — all through tool calls.

In this project, a "book" is the canonical registered unit of reference
material (not only traditional books): manuals, specs, PDFs, EPUBs, and other
long-form artifacts can all be tracked through the same lifecycle.

## MCP tools

| Tool | Description |
|------|-------------|
| `search` | Semantic search across indexed books. Filter by subject, library, or book_id. |
| `text_search` | Literal substring search for part numbers, error codes, exact strings. |
| `upload_book` | Returns the HTTP upload endpoint URL and a curl example. |
| `ingest_book` | Register a book already on the data volume (by path). |
| `extract_book` | Extract a book to markdown via Modal cloud GPU. |
| `index_book` | Embed chunks and store in pgvector. |
| `list_books` | List all books with metadata and status. |
| `update_book` | Tag books with subjects and library collections. |
| `delete_book` | Remove a book record and its vectors. |
| `suggest_tags` | Auto-suggest subjects and library based on content. |
| `book_status` | Pipeline statistics (counts by status, chunk totals). |
| `library_profile` | Oriented summary for agent onboarding. |

## Quick start

### Prerequisites

- Python 3.12+
- PostgreSQL with [pgvector](https://github.com/pgvector/pgvector) extension
- [Modal](https://modal.com/) account (for cloud extraction)

### 1. Set up the database

```bash
createdb librarian
psql librarian -c 'CREATE EXTENSION IF NOT EXISTS vector'
```

### 2. Install and configure

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[serve]"
```

Copy the example config and edit it:

```bash
cp config/settings.local.example.yaml config/settings.local.yaml
```

Set your PostgreSQL connection and embedding device:

```yaml
# config/settings.local.yaml
vector_store:
  backend: pgvector
  pgvector_url: postgresql://localhost:5432/librarian

embedding:
  model: BAAI/bge-base-en-v1.5
  device: cpu  # or mps (Apple Silicon) or cuda
```

Or use environment variables:

```bash
export LIBRARIAN_DB_URL=postgresql://localhost:5432/librarian
export LIBRARIAN_EMBEDDING_DEVICE=cpu
```

### 3. Set up Modal (for extraction)

```bash
pip install -e ".[cloud]"
modal setup
```

### 4. Run the MCP server

```bash
librarian-serve
```

The server starts on `http://localhost:8811` with streamable HTTP transport.

### 5. Upload and process a book

```bash
# Upload
curl -F file=@book.pdf -F title="My Book" http://localhost:8811/upload
# Returns: {"book_id": 1, ...}

# Then via MCP tools (or curl to MCP endpoint):
# extract_book(book_id=1)   — extract to markdown
# index_book(book_id=1)     — embed and index
# search("your query")      — search
```

## Docker

Build and run with Docker Compose:

```bash
# Create .env with your config
cat > .env <<EOF
LIBRARIAN_DB_URL=postgresql://host.docker.internal:5432/librarian
LIBRARIAN_DATA_ROOT=/data/librarian
LIBRARIAN_EMBEDDING_DEVICE=cpu
LIBRARIAN_PUBLIC_URL=http://agents.local:8811
EOF

docker compose up
```

The image bakes in the embedding model (~4.5 GB total) so there's no download
at startup. Modal credentials for cloud extraction go in `.env` as
`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`.

## Deployment topology

- Live app host: `agents.local` (Mac mini)
- Live PostgreSQL host for Librarian: `agents.local`
- Dev/agent sessions may run on another machine; verify target host before
  operational commands.

For safe migration workflow, see `docs/DB_MAINTENANCE.md`.

### One-command deploy

`make deploy` now performs:

1. Docker build (tag defaults to `git describe --always --dirty`)
2. Push to `agents.local:5000`
3. Remote `docker compose -f docker-compose.prod.yml up -d` on `agents.local`

Container startup runs `alembic upgrade head` before launching MCP server, so
migrations are applied at boot (no separate migration step).

Use preflight checks explicitly:

```bash
make deploy-preflight
```

`make deploy` also runs preflight automatically before build/push/deploy.

Defaults in `Makefile`:

- `REGISTRY=agents.local:5000`
- `DEPLOY_HOST=agents.local`
- `DEPLOY_PATH=/Users/dmichael/projects/librarian`

Override example:

```bash
make deploy REGISTRY=agents.local:5000 DEPLOY_HOST=agents.local DEPLOY_PATH=/Users/dmichael/projects/librarian
```

Requirement on `agents.local`: `.env.librarian` must exist and include runtime
settings (`LIBRARIAN_DB_URL`, `LIBRARIAN_DATA_ROOT`, `LIBRARIAN_PUBLIC_URL`,
`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, etc.).

## Connecting to an agent

Add the librarian MCP server to your agent's config. For Claude Code, add to
`~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "librarian": {
      "type": "streamable-http",
      "url": "http://localhost:8811/mcp"
    }
  }
}
```

The agent can then call `library_profile()` to discover what's available and
`search()` to query the knowledge base.

## Tagging and organization

Books are organized with a slash-separated subject taxonomy and named library
collections:

```python
update_book(book_id=1, subjects=["therapy/cbt", "psychology"], library="therapy-core")

# Then filter searches:
search("cognitive distortions", subjects=["therapy/*"])
search("emotion regulation", library="therapy-core")
```

Use `suggest_tags(book_id)` to get automatic suggestions based on content.

## Configuration

| Setting | Config key | Env var | Default |
|---------|-----------|---------|---------|
| Database URL | `vector_store.pgvector_url` | `LIBRARIAN_DB_URL` | — |
| Embedding device | `embedding.device` | `LIBRARIAN_EMBEDDING_DEVICE` | `mps` |
| Data root | — | `LIBRARIAN_DATA_ROOT` | `~/data/librarian` |
| Embedding model | `embedding.model` | — | `BAAI/bge-base-en-v1.5` |
| Chunk size | `chunking.chunk_size` | — | 512 tokens |

Config loads from `config/settings.local.yaml` if present, otherwise
`config/settings.yaml`. Environment variables override both.

## Project structure

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
  settings.yaml       Default configuration
  taxonomy.yaml       Subject taxonomy definitions
```

## Development

```bash
pip install -e ".[serve]"
pip install pytest ruff

ruff check src/
pytest
```

## License

MIT
