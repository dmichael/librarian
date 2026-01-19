#!/bin/bash
# Ingest PDFs/EPUBs from intake to Calibre
# Usage: make-ingest.sh <intake_dir> <library_path> <marker_dir> [--single]
#        With --single, first arg is a file path instead of directory
set -euo pipefail

INPUT="$1"
LIBRARY="$2"
MARKER_DIR="$3"
SINGLE_MODE="${4:-}"
FAILED_DIR="${MARKER_DIR}/../failed"

mkdir -p "$MARKER_DIR" "$FAILED_DIR"

if [ "$SINGLE_MODE" = "--single" ]; then
    # Single file mode
    files=("$INPUT")
else
    # Directory mode
    shopt -s nullglob
    files=("$INPUT"/*.pdf "$INPUT"/*.epub)
    shopt -u nullglob
fi

if [ ${#files[@]} -eq 0 ]; then
    exit 0
fi

for f in "${files[@]}"; do
    [ -f "$f" ] || continue

    # Use shasum on macOS (sha256sum on Linux)
    if command -v shasum &>/dev/null; then
        hash=$(shasum -a 256 "$f" | cut -c1-16)
    else
        hash=$(sha256sum "$f" | cut -c1-16)
    fi

    marker="$MARKER_DIR/$hash.id"
    failed="$FAILED_DIR/$hash.failed"

    # Skip if already processed or failed
    [ -f "$marker" ] && continue
    [ -f "$failed" ] && continue

    echo "Ingesting: $(basename "$f")"
    output=$(calibredb add --library-path "$LIBRARY" --automerge ignore "$f" 2>&1) || true

    # Parse Calibre output for book ID
    if [[ "$output" =~ "Added book ids: "([0-9]+) ]]; then
        echo "${BASH_REMATCH[1]}" > "$marker"
        echo "  -> Calibre ID ${BASH_REMATCH[1]}"
    elif [[ "$output" =~ "already exist in the database" ]]; then
        # Book already exists - mark as done with unknown ID
        echo "exists" > "$marker"
        echo "  -> Already in Calibre"
    else
        echo "$output" > "$failed"
        echo "  -> Failed (see $failed)"
    fi
done
