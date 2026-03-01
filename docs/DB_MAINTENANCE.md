# Database Maintenance

Safe operational workflow for Librarian DB migrations.

## Where to run

Run these operations against the live host:

- Host: `agents.local`
- Database: `librarian` on `agents.local`

If you are on a dev machine, run through SSH to `agents.local` or run directly
on the Mac mini shell.

## Safe migration wrapper

Use:

```bash
.venv/bin/python scripts/db_safe_migrate.py
```

This performs prechecks and creates a repo-local snapshot in:

```text
.db_backups/<timestamp>/
```

To actually run Alembic:

```bash
.venv/bin/python scripts/db_safe_migrate.py --apply
```

## What the wrapper protects

- Snapshots `books` schema/data (if present)
- Snapshots `alembic_version` (if present)
- Captures row counts for:
  - `data_librarian_full`
  - `data_librarian_equations`
  - `data_librarian_chapters`
- Verifies those pgvector counts are unchanged after migration
- Refuses non-`librarian` DB names unless explicitly overridden

## Example from dev machine

```bash
ssh agents.local "cd /Users/dmichael/projects/librarian && .venv/bin/python scripts/db_safe_migrate.py --apply"
```

## Notes

- This workflow is non-destructive by design.
- Backups are stored in-repo working directory (not `/tmp`) and are gitignored.
- For custom DB targets, use `--db-url ...` and optionally
  `--allow-non-librarian-db` if intentional.
