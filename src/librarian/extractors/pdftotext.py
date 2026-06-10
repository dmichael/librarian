"""Poppler pdftotext extractor.

Writes a plain-text sidecar under raw/pdftotext/. This is intentionally not a
primary content extractor yet: Marker remains the source for indexed blocks.
The goal is to preserve a page-delimited, layout-aware text view that can later
be used to repair code-heavy PDF extraction failures.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from librarian.files import pdftotext_dir


class PdfToTextExtractionError(RuntimeError):
    """Raised when pdftotext fails to extract a PDF."""


def extract(source: Path, book_dir: Path, *, timeout: float = 180.0) -> dict:
    """Run ``pdftotext -layout`` and write raw/pdftotext artifacts.

    Produces:
      - raw/pdftotext/document.txt   full layout-aware text
      - raw/pdftotext/pages.json     page-delimited text records
      - raw/pdftotext/metadata.json  command/provenance summary

    Returns {"page_count": N}. Raises on failure and leaves any previous
    pdftotext artifacts untouched.
    """
    if not source.exists():
        raise FileNotFoundError(source)

    command = ["pdftotext", "-layout", "-enc", "UTF-8", str(source), "-"]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise PdfToTextExtractionError("pdftotext executable not found") from e
    except subprocess.TimeoutExpired as e:
        raise PdfToTextExtractionError(f"pdftotext timed out after {timeout}s") from e

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise PdfToTextExtractionError(
            f"pdftotext exited {result.returncode}: {stderr[:300]}"
        )

    text = result.stdout
    pages = _split_pages(text)

    out_dir = pdftotext_dir(book_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "document.txt").write_text(text)
    (out_dir / "pages.json").write_text(json.dumps(pages, indent=2) + "\n")
    (out_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tool": "pdftotext",
                "command": command[:4] + ["<source>", "-"],
                "layout": True,
                "encoding": "UTF-8",
                "page_count": len(pages),
            },
            indent=2,
        )
        + "\n"
    )

    return {"page_count": len(pages)}


def _split_pages(text: str) -> list[dict]:
    """Split pdftotext output on form-feed page separators."""
    raw_pages = text.split("\f")
    if raw_pages and raw_pages[-1] == "":
        raw_pages = raw_pages[:-1]
    return [
        {
            "page": index,
            "text": page_text.rstrip("\n"),
        }
        for index, page_text in enumerate(raw_pages, start=1)
    ]
