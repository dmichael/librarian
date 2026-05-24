"""Pluggable raw extraction backends used by extraction QA.

Marker remains the primary PDF extractor today. This module defines the small
interface for raw extractors that can be run alongside Marker and compared
against it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class ExtractorResult:
    name: str
    success: bool
    output_path: str | None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Extractor(Protocol):
    name: str

    def run(self, source: Path, output_dir: Path) -> ExtractorResult:
        """Run the extractor and write artifacts under output_dir."""


class PdfTextExtractor:
    """Embedded-text baseline using poppler's pdftotext."""

    name = "pdftext"

    def run(self, source: Path, output_dir: Path) -> ExtractorResult:
        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            return ExtractorResult(
                name=self.name,
                success=False,
                output_path=None,
                error="pdftotext not found; install poppler-utils/poppler",
            )

        raw_dir = output_dir / "raw" / self.name
        raw_dir.mkdir(parents=True, exist_ok=True)
        text_path = raw_dir / "layout.txt"

        try:
            result = subprocess.run(
                [pdftotext, "-layout", str(source), str(text_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as e:
            return ExtractorResult(
                name=self.name,
                success=False,
                output_path=None,
                error=str(e),
            )

        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or "pdftotext failed"
            return ExtractorResult(
                name=self.name,
                success=False,
                output_path=None,
                error=error,
            )

        return ExtractorResult(
            name=self.name,
            success=True,
            output_path=str(text_path),
        )


def default_raw_extractors() -> list[Extractor]:
    """Raw extractors that are cheap and safe enough to run with every PDF."""
    return [PdfTextExtractor()]
