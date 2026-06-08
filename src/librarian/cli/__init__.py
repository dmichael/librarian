"""Top-level CLI: librarian <command> [args...]"""
from __future__ import annotations

import sys

from dotenv import load_dotenv


COMMANDS: dict[str, str] = {
    "extract": "librarian.cli.extract:main",
    "index": "librarian.cli.index:main",
    "query": "librarian.query:main",
    "serve": "librarian.mcp_server:main",
}


def main() -> None:
    load_dotenv()

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("usage: librarian <command> [args...]")
        print(f"\ncommands: {', '.join(sorted(COMMANDS))}")
        raise SystemExit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"unknown command: {cmd}", file=sys.stderr)
        print(f"available: {', '.join(sorted(COMMANDS))}", file=sys.stderr)
        raise SystemExit(2)

    module_path, func_name = COMMANDS[cmd].rsplit(":", 1)

    sys.argv = sys.argv[1:]
    sys.argv[0] = f"librarian {cmd}"

    from importlib import import_module
    mod = import_module(module_path)
    getattr(mod, func_name)()
