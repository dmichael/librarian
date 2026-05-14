# Agent Guide

Core behavior and safety rules for agents working in this repo.

Librarian is reference infrastructure for development agents: it provides
primary searchable and verifiable sources during active work. "Books" are the
registered source artifacts in this pipeline.

## Read first

1. `SPEC.md` — design intent
2. `README.md` — setup and current interface
3. `docs/QUICKSTART.md` — bootstrap commands
4. `docs/AGENT_CONTEXT.md` — operational/context details (non-rule reference)
5. `docs/DB_MAINTENANCE.md` — safe DB migration workflow

## Core behavior rules

1. Verify execution target before operations.
   - Live app + database run on `ms-01.local` (Linux x86 box).
   - Agent sessions may run on a different dev machine.
   - Do not assume local `localhost` is the live Librarian DB/service.
   - Do not evaluate `ms-01.local` from normal dev workflow unless the user
     explicitly requests it (or asks for recovery after a confidence-breaking failure).
2. Protect indexed data unless explicitly instructed otherwise.
   - pgvector collections are expensive to rebuild.
   - Do not drop/truncate/overwrite `data_*` pgvector tables by default.
   - Keep migrations idempotent and scoped to required tables/columns only.
3. Prefer small, reversible changes that preserve end-to-end pipeline health.
4. Run project commands via the repository venv (`.venv/bin/...`).
5. Never commit external data roots (`~/data/librarian/`) to git.
6. Keep taxonomy edits backward-compatible (existing tags remain valid).
