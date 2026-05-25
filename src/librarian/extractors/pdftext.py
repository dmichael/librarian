"""pdftotext extractor.

Writes embedded PDF text (layout-preserving) to raw/pdftext/layout.txt.
Used as a glyph-level cross-check against Marker for the equations domain.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


NAME = "pdftext"
ARTIFACT_REL_PATH = Path("raw") / NAME / "layout.txt"


def extract(source: Path, book_dir: Path) -> None:
    """Run pdftotext -layout on source, write to book_dir/raw/pdftext/layout.txt.

    Raises FileNotFoundError if pdftotext isn't installed, RuntimeError if
    pdftotext exits non-zero, and propagates any subprocess error.
    """
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        raise FileNotFoundError(
            "pdftotext not found on PATH; install poppler-utils (or poppler on macOS)"
        )

    out_path = book_dir / ARTIFACT_REL_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [pdftotext, "-layout", str(source), str(out_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "pdftotext failed"
        raise RuntimeError(f"pdftotext exited {result.returncode}: {message}")
