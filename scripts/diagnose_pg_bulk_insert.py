"""Reproduce the 'SSL connection has been closed unexpectedly' insert failure.

Run inside the librarian container (or any host whose network path to
Postgres you want to test). Connects via LIBRARIAN_DB_URL, then inserts
payloads of increasing size into a TEMP table (auto-dropped, no schema
footprint) to find the size at which the connection dies.

Usage:
    python diagnose_pg_bulk_insert.py
"""
import os
import sys
import time

import psycopg2

SIZES_MB = [0.1, 1, 5, 10, 20]


def main() -> int:
    url = os.environ.get("LIBRARIAN_DB_URL")
    if not url:
        print("LIBRARIAN_DB_URL not set", file=sys.stderr)
        return 2

    conn = psycopg2.connect(url)
    conn.autocommit = True
    print(f"connected: server={conn.server_version} ssl={conn.info.ssl_in_use}")

    cur = conn.cursor()
    cur.execute("CREATE TEMP TABLE _diag_bulk (id serial, payload text)")

    for size_mb in SIZES_MB:
        payload = "x" * int(size_mb * 1024 * 1024)
        t0 = time.monotonic()
        try:
            cur.execute("INSERT INTO _diag_bulk (payload) VALUES (%s)", (payload,))
            print(f"  {size_mb:>5} MB insert OK in {time.monotonic() - t0:.2f}s")
        except psycopg2.OperationalError as e:
            print(f"  {size_mb:>5} MB insert FAILED after {time.monotonic() - t0:.2f}s: {e}")
            return 1
    print("all sizes passed — payload size alone does not reproduce")
    return 0


if __name__ == "__main__":
    sys.exit(main())
