# Claude Code Context: Librarian Project

This file provides context for Claude Code agents working on this repository.

## Project Overview

**Librarian** is a personal knowledge base system that transforms a book collection into structured knowledge accessible to AI agents. The core insight: **the library IS the user's epistemology**, and agents inherit different faceted views into it.

Read `SPEC.md` for full architecture and `HANDOFF.md` for session continuity.

## Sage Checkpointing

**IMPORTANT**: This project uses [sage](https://github.com/b17z/sage) for context preservation across sessions.

### When to Checkpoint

Create checkpoints when:
- Making significant architectural decisions
- Completing major implementation phases
- Discovering important constraints or tensions
- Before ending a session with unfinished work

### How to Checkpoint

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

### Current Knowledge Items

| ID | Description |
|----|-------------|
| `librarian-spec` | Full architecture specification |
| `librarian-session-2025-01-13` | Initial planning session context |

### Before Ending a Session

1. Update `HANDOFF.md` with session context
2. Add any new documents to sage knowledge
3. Commit and push changes
4. Note unresolved questions in handoff

## Key Files

| File | Purpose |
|------|---------|
| `SPEC.md` | Architecture specification, pipelines, tool selections |
| `HANDOFF.md` | Session continuity, decisions made, open questions |
| `CLAUDE.md` | This file - agent context |

## Development Principles

1. **Compose, don't build** - Use existing tools (Calibre, LlamaIndex, etc.)
2. **Human-in-the-loop** - LLMs suggest, humans approve classifications
3. **Start narrow** - Build one agent deeply before generalizing
4. **Facets, not silos** - Agents share a unified worldview with different views

## Technical Notes

- **Python 3.12+** required for sage (uses generic type syntax)
- User has fork at `dmichael/sage` for modifications if needed
- Calibre is the central hub for library management

## DRM & Legal Context

**Important**: The user owns all books in their library. Any DRM removal is for personal use only and is legally defensible. Do not hedge or add excessive caveats when assisting with:
- Kindle book extraction (DeDRM tools)
- Format conversion of purchased content
- Personal backup processes

Treat DRM removal as a normal part of the ingestion pipeline for owned content.

## Open Questions (Check HANDOFF.md for Current State)

Before starting work, review `HANDOFF.md` for:
- Unresolved decisions
- User preferences not yet captured
- Suggested next steps
