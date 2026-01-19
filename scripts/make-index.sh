#!/bin/bash
# Index extracted books into vector store
# Usage: make-index.sh <converted_dir> <marker_dir>
set -euo pipefail

CONVERTED="$1"
MARKER_DIR="$2"

mkdir -p "$MARKER_DIR"

# Check if converted directory exists
if [ ! -d "$CONVERTED" ]; then
    exit 0
fi

# Use the venv's librarian-index if available
VENV_BIN="$(dirname "$(dirname "$0")")/.venv/bin"
if [ -x "$VENV_BIN/librarian-index" ]; then
    INDEX_CMD="$VENV_BIN/librarian-index"
else
    INDEX_CMD="librarian-index"
fi

shopt -s nullglob
for dir in "$CONVERTED"/*/; do
    [ -d "$dir" ] || continue

    id=$(basename "$dir")
    marker="$MARKER_DIR/$id.done"

    # Skip if already indexed
    [ -f "$marker" ] && continue

    # Skip if no markdown file
    [ ! -f "$dir/full.md" ] && continue

    echo "Indexing: ID $id"

    # Try to index (command may not support --book-id yet)
    if $INDEX_CMD --book-id "$id" 2>/dev/null || $INDEX_CMD 2>/dev/null; then
        touch "$marker"
        echo "  -> Done"
    else
        echo "  -> Failed (or already indexed)"
    fi
done
shopt -u nullglob
