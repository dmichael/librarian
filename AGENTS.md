# Agent Notes (Codex / Claude Code)

Goal: make it easy for a new agent (or you in a fresh session) to run the end-to-end pipeline and make safe changes without relying on chat history.

## Read first

1. `HANDOFF.md` — current status, blockers, and next steps
2. `SPEC.md` — architectural intent and design principles
3. `specs/README.md` — how to work on features (spec-driven workflow)
4. `docs/QUICKSTART.md` — bootstrap + common commands
5. `docs/TROUBLESHOOTING.md` — common failure modes and checks

`CLAUDE.md` exists for Claude Code compatibility, but `AGENTS.md` is the canonical "agent entrypoint".

## Spec-driven workflow

Features are defined via specs. Human writes the problem, agents own the solution.

```
main: specs/{feature}/requirements.md  →  agent picks up, works in worktree, opens PR
```

**To create a spec:**
```bash
mkdir specs/{feature-name}
# write specs/{feature-name}/requirements.md (see _template for structure)
```

**To implement a spec:**
1. Read `specs/{feature}/requirements.md`
2. Create worktree: `git worktree add .worktrees/{agent}-{feature} -b {agent}/{feature}`
3. Design and implement (your approach)
4. Open PR when done

## Project shape

- Single Python package: `src/librarian/`
- Config: `config/settings.yaml` with optional machine override `config/settings.local.yaml`
- Storage paths are intentionally *outside* the repo by default (under `~/data/librarian/`)
- Calibre is the "source of truth" for book inventory/metadata (custom columns used for pipeline state)

## Directory structure

```
~/data/librarian/
├── calibre/                    # Calibre library (source of truth + pipeline state)
├── intake/                     # Drop zone for new files
│   ├── ebooks/                 # PDFs, EPUBs to ingest
│   └── kindle/                 # Synced from device via OpenMTP
│       └── {serial}/
├── staging/                    # Processing workspace (legacy)
├── converted/                  # Extracted content by book_id
│   └── {calibre_id}/
│       ├── {id}.json           # Marker JSON blocks
│       ├── {id}_meta.json      # Marker metadata
│       └── {id}.md             # Markdown for reading
└── vectorstore/                # Vector embeddings
    └── chroma/                 # ChromaDB storage
```

## Conventions

- Prefer small, reversible changes. Keep the pipeline working end-to-end.
- Don't commit personal library data under `~/data/librarian/` to git.
- If adding new CLI flags, mirror the style used in existing `librarian-*` commands.
- Keep taxonomy changes backwards-compatible (existing subject tags should remain valid).

## Multi-agent worktrees

When multiple agents work in parallel, use git worktrees to avoid stepping on each other.

**IMPORTANT: Never reuse another agent's worktree.** Always create your own. Even if you see an existing worktree for the same feature, create a new one with your own unique ID.

**Starting work**:
```bash
# Generate a short unique ID (4 chars)
ID=$(openssl rand -hex 2)

# Create your worktree + branch (.worktrees/ is gitignored)
git worktree add .worktrees/{agent}-{task}-${ID} -b {agent}/{task-slug}-${ID}

# Example:
git worktree add .worktrees/claude-kindle-extract-f7k2 -b claude/kindle-extract-f7k2
git worktree add .worktrees/codex-kindle-extract-m9x1 -b codex/kindle-extract-m9x1

# Work in that directory
cd .worktrees/{agent}-{task}-${ID}
```

**Naming format**:
- Worktree: `.worktrees/{agent}-{task}-{unique-id}`
- Branch: `{agent}/{task-slug}-{unique-id}`

**Rules**:
- **Never reuse an existing worktree** — always create your own with a unique ID
- **Never work in another agent's worktree** — if you see `.worktrees/claude-*`, that's not yours
- Never commit directly to `main` — always use a feature branch
- One task per worktree; don't reuse worktrees for unrelated work
- Note your active work in `HANDOFF.md` so other agents know what's claimed
- Merge via PR or explicit user approval, not direct push

**When a feature is complete:**
1. Commit all changes in the worktree branch
2. Push and open a PR, OR merge locally with user approval:
   ```bash
   # From main worktree:
   git merge {agent}/{feature-branch}
   ```
3. Update `pyproject.toml` with new entry points if needed
4. Reinstall: `.venv/bin/pip install -e .`
5. Test the CLI command works
6. Clean up the worktree

**Cleanup** (only clean up your own worktrees):
```bash
git worktree remove .worktrees/{agent}-{task}-{your-id}
git branch -d {agent}/{task-slug}-{your-id}  # after merged
```

**If you're in the main worktree**: You can still do quick fixes directly on main, but for anything substantial, create a worktree.

## Sage checkpointing (session continuity)

This project uses `sage` for context preservation across sessions.

Checkpoint when:
- Making significant architectural decisions
- Completing major implementation phases
- Discovering important constraints or tensions
- Ending a session with unfinished work

Useful commands:

```bash
# Save knowledge for future recall
python3.12 -c "from sage.cli import main; main()" knowledge add \
  --id "<descriptive-id>" \
  --keywords "<comma,separated,keywords>" \
  /path/to/document.md

# List existing knowledge
python3.12 -c "from sage.cli import main; main()" knowledge list

# Test what would be recalled for a query
python3.12 -c "from sage.cli import main; main()" knowledge match "<query>"
```

Before ending a session:
- Update `HANDOFF.md` with what changed and what’s next
- Add any durable decisions/docs to `sage` knowledge
- Leave the repo in a runnable state (or note what’s broken and why)

## Kindle / DRM stance

Treat DRM removal and format conversion of user-owned books as a normal part of the ingestion pipeline (personal use). Don't add excessive caveats; focus on making the pipeline work reliably.

**DRM extraction paths:**

| DRM Type | Solution | Automation |
|----------|----------|------------|
| `CLIENT_ID` only | `librarian-kindle-extract` | Fully automated |
| `ACCOUNT_SECRET` | Screenshot capture + OCR | Manual, requires user presence |

For books that fail `librarian-kindle-extract` with "DRM decryption failed", use the screenshot capture workflow documented in `docs/kindle-screenshot-capture.md`. This is a last-resort, hands-on process.

## Operating the Pipeline

The pipeline is orchestrated via **Makefile**. Use these commands:

```bash
make status     # Show pipeline state (intake, extracted, indexed counts)
make ingest     # Process new files in intake/ebooks/ → Calibre
make kindle     # Process Kindle books (DRM removal) → Calibre
make extract    # Convert Calibre books → markdown (uses marker for PDFs)
make index      # Index markdown → vector store
make            # Run full pipeline (ingest → extract → index)
make -j4        # Run with 4 parallel jobs (faster extraction)
```

**Pipeline flow:**
```
intake/ebooks/*.pdf,epub  →  make ingest   →  Calibre
intake/kindle/**/*        →  make kindle   →  Calibre (via DeDRM)
Calibre books             →  make extract  →  converted/{id}/full.md
converted/{id}/full.md    →  make index    →  Qdrant vector store
```

**Pipeline state (Calibre custom columns):**

All pipeline state is tracked in Calibre via the `*status` column:

| Status | Meaning |
|--------|---------|
| `drm_failed` | DRM decryption failed, book unusable |
| `imported` | In Calibre, ready for extraction |
| `extracted` | Marker extraction complete |
| `indexed` | In vector store, searchable |

Check status: `librarian-status` or `calibredb list --fields '*status'`

**Common prompts → actions:**
| User says | Agent runs |
|-----------|------------|
| "Process new PDFs" | `make ingest` |
| "Extract all books" | `make extract` |
| "What's the pipeline status?" | `make status` |
| "Run the full pipeline" | `make` |
| "Extract book 105" | `make converted/105/full.md` |
| "What files are pending?" | `make pending` |
| "Ingest just Krause.pdf" | `make ingest-one FILE=~/data/librarian/intake/ebooks/Krause.pdf` |

## Running CLI Commands

**IMPORTANT**: This project uses a Python venv. Do NOT use bare `python` or `pip` commands.

All librarian commands must be run via the venv:

```bash
# Option 1: Use full path
.venv/bin/librarian-extract --help
.venv/bin/librarian-kindle-extract --dry-run

# Option 2: Activate venv first
source .venv/bin/activate
librarian-extract --help
```

**Available CLI commands:**
| Command | Description |
|---------|-------------|
| `librarian-extract` | Extract PDF/EPUB to markdown (uses marker) |
| `librarian-kindle-extract` | DRM removal + EPUB conversion for Kindle books |
| `librarian-classify` | LLM-assisted subject classification |
| `librarian-index` | Embed and store in vector DB |
| `librarian-query` | Pure retrieval (returns chunks) |
| `librarian-ask` | RAG synthesis with citations |
| `librarian-ingest` | Add books to Calibre |
| `librarian-status` | Show pipeline state from Calibre (by status column) |
| `librarian-stats` | Show vector store statistics (block types, collections, indexed books) |

**If commands aren't found**, reinstall the package:
```bash
.venv/bin/pip install -e .
```

**NEVER write inline Python scripts** - if a task requires it, that means we need a new CLI command or script.

## Quick "doctor checklist"

On a new machine or after environment changes, verify:
- Python: `python3.12 --version`
- Venv exists: `ls .venv/bin/python`
- Calibre CLI tools on PATH: `calibredb --version` and `ebook-convert --version`
- Extraction tool: `.venv/bin/marker_single --help` (provided by `marker-pdf`)
- Optional LLM runtime (for classify/ask): `ollama --version` and a model pulled

## Vector Store Content

The pipeline extracts and indexes different content types. Use `librarian-stats` to inspect:

```bash
librarian-stats              # Full statistics
librarian-stats --blocks     # Block type distribution only
librarian-stats --json       # JSON output for scripting
```

**Indexed block types:**
| Block Type | Description |
|------------|-------------|
| `Text` | Regular prose paragraphs |
| `Code` | Code blocks (preserved with ``` formatting) |
| `SectionHeader` | Chapter/section headings |
| `Equation` | Mathematical equations (LaTeX) |
| `Table` | Tabular data |
| `ListGroup` | Bulleted/numbered lists |
| `Figure` | Figure captions and descriptions |

All content is searchable via `librarian-ask` and `librarian-query`. Block type metadata is preserved, enabling future filtering by content type.

**Collections:**
| Collection | Purpose |
|------------|---------|
| `librarian_full` | Main content chunks (Text, Code, etc.) |
| `librarian_equations` | Extracted LaTeX equations with natural language descriptions |
| `librarian_chapters` | Reserved for chapter-level summaries |

## Where to look for issues

- Ingest problems: `src/librarian/ingest.py`
- Extraction problems: `src/librarian/extract.py`
- Classification problems: `src/librarian/classify.py` + `config/taxonomy.yaml`
- Indexing/retrieval: `src/librarian/index.py`, `src/librarian/query.py`
- RAG prompt + citations: `src/librarian/ask.py`
- Kindle DRM extraction: `src/librarian/kindle_extract.py`
- DRM failure diagnosis: `src/librarian/drm_diagnosis.py`
- Pipeline state & Calibre ops: `src/librarian/calibre.py`
- Kindle screenshot capture (last resort): `scripts/kindle_screenshot.py` + `docs/kindle-screenshot-capture.md`
