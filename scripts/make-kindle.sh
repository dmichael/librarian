#!/bin/bash
# Handle Kindle books (DRM stripping via Calibre/DeDRM)
# Usage: make-kindle.sh <kindle_dir> <library_path> <state_dir>
set -euo pipefail

KINDLE="$1"
LIBRARY="$2"
STATE="$3"

# Check if kindle intake exists and has content
if [ ! -d "$KINDLE" ]; then
    exit 0
fi

# Count kindle files
shopt -s nullglob
kindle_files=("$KINDLE"/**/*.azw* "$KINDLE"/**/*.kfx "$KINDLE"/**/*.mobi)
shopt -u nullglob

if [ ${#kindle_files[@]} -eq 0 ]; then
    exit 0
fi

# Use the venv's librarian-kindle-extract if available
VENV_BIN="$(dirname "$(dirname "$0")")/.venv/bin"
if [ -x "$VENV_BIN/librarian-kindle-extract" ]; then
    EXTRACT_CMD="$VENV_BIN/librarian-kindle-extract"
else
    EXTRACT_CMD="librarian-kindle-extract"
fi

# Delegate to existing command (it already handles idempotency via state.py)
echo "Processing Kindle books..."
$EXTRACT_CMD 2>&1 | while read -r line; do
    echo "  $line"
done
