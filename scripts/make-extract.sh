#!/bin/bash
# Extract Calibre books to markdown
# Usage: make-extract.sh <library_path> <converted_dir> <marker_dir>
set -euo pipefail

LIBRARY="$1"
CONVERTED="$2"
MARKER_DIR="$3"

mkdir -p "$MARKER_DIR"

# Get all book IDs from Calibre
calibredb list --library-path "$LIBRARY" --for-machine 2>/dev/null | \
  jq -r '.[].id' | \
while read -r id; do
    [ -z "$id" ] && continue

    marker="$MARKER_DIR/$id.done"
    output="$CONVERTED/$id/full.md"

    # Skip if already extracted
    [ -f "$marker" ] && continue

    # If output exists but no marker, create marker
    if [ -f "$output" ]; then
        touch "$marker"
        continue
    fi

    echo "Extracting: ID $id"

    # Use the venv's librarian-extract if available
    VENV_BIN="$(dirname "$(dirname "$0")")/.venv/bin"
    if [ -x "$VENV_BIN/librarian-extract" ]; then
        EXTRACT_CMD="$VENV_BIN/librarian-extract"
    else
        EXTRACT_CMD="librarian-extract"
    fi

    if $EXTRACT_CMD --book-id "$id"; then
        touch "$marker"
        echo "  -> Done"
    else
        echo "  -> Failed"
    fi
done
