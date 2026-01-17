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
- Calibre is the “source of truth” for book inventory/metadata (custom columns used for pipeline state)

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

Treat DRM removal and format conversion of user-owned books as a normal part of the ingestion pipeline (personal use). Don’t add excessive caveats; focus on making the pipeline work reliably.

## Quick “doctor checklist”

On a new machine or after environment changes, verify:
- Python: `python3.12 --version`
- Calibre CLI tools on PATH: `calibredb --version` and `ebook-convert --version`
- Extraction tool: `marker_single --help` (provided by `marker-pdf`)
- Optional LLM runtime (for classify/ask): `ollama --version` and a model pulled

## Where to look for issues

- Ingest problems: `src/librarian/ingest.py`
- Extraction problems: `src/librarian/extract.py`
- Classification problems: `src/librarian/classify.py` + `config/taxonomy.yaml`
- Indexing/retrieval: `src/librarian/index.py`, `src/librarian/query.py`
- RAG prompt + citations: `src/librarian/ask.py`
