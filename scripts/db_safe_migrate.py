#!/usr/bin/env python3
"""Safe, host-local migration wrapper for Librarian.

Goals:
1. Snapshot `books` + alembic state into repo-local backups (not /tmp)
2. Run Alembic upgrade only when explicitly requested (--apply)
3. Verify pgvector collection row counts are unchanged pre/post migration

Run from the dev machine — the script reads LIBRARIAN_DB_URL from .env and
targets the live DB on ms-01.local. Override with --db-url for non-default
targets.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# Allow running as a standalone script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from librarian.config import load_config
from librarian.vectorstore import get_collection_names

# LlamaIndex prefixes collection tables with 'data_'
VECTOR_TABLES = [
    f"data_{name}" for name in get_collection_names(load_config()).values()
]


def _run(cmd: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        cmd,
        text=True,
        capture_output=capture,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{stderr}")
    return (result.stdout or "").strip() if capture else ""


def _check_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Missing required binary: {name}")


def _redact_db_url(db_url: str) -> str:
    parts = urlsplit(db_url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return f"{parts.scheme}://{netloc}{parts.path}"


def _extract_db_name(db_url: str) -> str:
    path = urlsplit(db_url).path.strip("/")
    return path or ""


def _table_exists(db_url: str, table_name: str) -> bool:
    query = f"SELECT to_regclass('public.{table_name}') IS NOT NULL;"
    out = _run(["psql", db_url, "-At", "-c", query], capture=True)
    return out.lower() in {"t", "true", "1"}


def _table_count(db_url: str, table_name: str) -> int:
    query = f"SELECT COUNT(*) FROM public.{table_name};"
    out = _run(["psql", db_url, "-At", "-c", query], capture=True)
    return int(out)


def _alembic_version(db_url: str) -> str | None:
    if not _table_exists(db_url, "alembic_version"):
        return None
    out = _run(
        ["psql", db_url, "-At", "-c", "SELECT version_num FROM public.alembic_version LIMIT 1;"],
        capture=True,
    )
    return out or None


def _snapshot_table_schema(db_url: str, table_name: str, output_file: Path) -> None:
    _run(
        [
            "pg_dump",
            db_url,
            "--schema-only",
            "--no-owner",
            "--no-privileges",
            "--table",
            f"public.{table_name}",
            "-f",
            str(output_file),
        ]
    )


def _snapshot_table_data(db_url: str, table_name: str, output_file: Path) -> None:
    _run(
        [
            "pg_dump",
            db_url,
            "--data-only",
            "--no-owner",
            "--no-privileges",
            "--table",
            f"public.{table_name}",
            "-f",
            str(output_file),
        ]
    )


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _capture_vector_counts(db_url: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in VECTOR_TABLES:
        if _table_exists(db_url, table):
            counts[table] = _table_count(db_url, table)
    return counts


def _run_alembic_upgrade(repo_root: Path) -> None:
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError as exc:
        raise RuntimeError(
            "Alembic is not installed in this Python environment. "
            "Install with .venv/bin/pip install -e '.[serve]' on the target host."
        ) from exc

    cfg = Config(str(repo_root / "alembic.ini"))
    command.upgrade(cfg, "head")


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe migration runner for Librarian DB")
    parser.add_argument(
        "--db-url",
        default=None,
        help="Override database URL. Defaults to LIBRARIAN_DB_URL or config value.",
    )
    parser.add_argument(
        "--backup-root",
        default=".db_backups",
        help="Directory for snapshots (default: .db_backups).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually run migration (default is snapshot/precheck only).",
    )
    parser.add_argument(
        "--allow-non-librarian-db",
        action="store_true",
        help="Allow DB names other than 'librarian'.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    os.chdir(repo_root)

    config = load_config()
    db_url = args.db_url or os.getenv("LIBRARIAN_DB_URL") or config["vector_store"]["pgvector_url"]
    db_name = _extract_db_name(db_url)

    if not args.allow_non_librarian_db and db_name != "librarian":
        print(
            f"Refusing to run against DB '{db_name}'. "
            "Use --allow-non-librarian-db if this is intentional.",
            file=sys.stderr,
        )
        return 2

    _check_binary("psql")
    _check_binary("pg_dump")

    started_at = datetime.now(timezone.utc)
    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    backup_dir = (repo_root / args.backup_root / stamp).resolve()
    backup_dir.mkdir(parents=True, exist_ok=False)

    print(f"DB URL: {_redact_db_url(db_url)}")
    print(f"Backup dir: {backup_dir}")

    vector_counts_pre = _capture_vector_counts(db_url)
    alembic_pre = _alembic_version(db_url)

    # Snapshot books table if present
    books_exists = _table_exists(db_url, "books")
    if books_exists:
        _snapshot_table_schema(db_url, "books", backup_dir / "books_schema.sql")
        _snapshot_table_data(db_url, "books", backup_dir / "books_data.sql")
    else:
        (backup_dir / "books_missing.txt").write_text("books table not present at snapshot time\n")

    # Snapshot alembic state if present
    if _table_exists(db_url, "alembic_version"):
        _snapshot_table_data(db_url, "alembic_version", backup_dir / "alembic_version_data.sql")

    manifest_pre = {
        "started_at_utc": started_at.isoformat(),
        "db_url_redacted": _redact_db_url(db_url),
        "db_name": db_name,
        "books_table_present": books_exists,
        "alembic_version_pre": alembic_pre,
        "vector_counts_pre": vector_counts_pre,
        "apply_requested": bool(args.apply),
    }
    _write_json(backup_dir / "manifest_pre.json", manifest_pre)

    if not args.apply:
        print("Snapshot complete. Re-run with --apply to execute alembic upgrade.")
        return 0

    print("Running alembic upgrade head...")
    _run_alembic_upgrade(repo_root)

    vector_counts_post = _capture_vector_counts(db_url)
    alembic_post = _alembic_version(db_url)
    changed_vector_tables = {
        table: {"pre": vector_counts_pre.get(table), "post": vector_counts_post.get(table)}
        for table in sorted(set(vector_counts_pre) | set(vector_counts_post))
        if vector_counts_pre.get(table) != vector_counts_post.get(table)
    }

    finished_at = datetime.now(timezone.utc)
    manifest_post = {
        "finished_at_utc": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "alembic_version_post": alembic_post,
        "vector_counts_post": vector_counts_post,
        "vector_counts_changed": changed_vector_tables,
    }
    _write_json(backup_dir / "manifest_post.json", manifest_post)

    if changed_vector_tables:
        print("ERROR: pgvector row counts changed unexpectedly:", file=sys.stderr)
        print(json.dumps(changed_vector_tables, indent=2), file=sys.stderr)
        return 3

    print("Migration applied successfully with no pgvector row-count changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
