"""Decide which backend extracts a PDF: the shared Spark GPU, or Modal.

The decision is a **pure function of the PDF itself** — never a live query of
the Spark's memory. Same PDF in, same decision out, every time. That keeps the
choice deterministic, reproducible, and unit-testable with no infrastructure.

Design:
  pdf_signals(path)  -> PdfSignals      # the only I/O (pdfinfo/pdfimages)
  decide_backend(sig) -> RoutingDecision # pure: signals in, decision out
  route_pdf(path)    -> RoutingDecision  # convenience composition of the two

Why these signals: Marker rasterizes every page and runs OCR on it, so its peak
memory tracks the *raster burden* — the total embedded image pixels it must
process, and the size of the largest single page (a single huge allocation is
its own failure mode). Page count and file size do not separate the cases
(a 600-page born-digital book is cheap; a 400-page book of full-page scans is
not). See scripts/detect_offload_signals.py for the calibration.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# Marker renders pages to bitmaps at a fixed target DPI regardless of source.
RENDER_DPI = 192

# Static thresholds. Because we deliberately do NOT check live Spark headroom,
# these assume a busy Spark and sit *below* the lowest observed failure, with
# margin. Calibrated from the backfill outcomes:
#   spark_ok  reached  img_mpix=841,  page_megapix=4.2
#   offloaded started img_mpix=1091, page_megapix=13.2
# so the lines fall in the gap. Refine offline as more outcomes accumulate;
# never by querying the Spark at decision time.
IMG_MPIX_MAX = 900.0     # total embedded image megapixels (cumulative burden)
PAGE_MEGAPIX_MAX = 6.0   # largest single rendered page (single-allocation risk)


class PdfSignals(BaseModel):
    """Intrinsic, deterministic signals read from a PDF."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pages: int
    img_mpix: float      # Σ embedded image pixels, in millions
    page_megapix: float  # page area at RENDER_DPI, in millions of pixels
    size_mb: float


class RoutingDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: str         # "spark" | "modal"
    reason: str
    signals: PdfSignals


def decide_backend(
    signals: PdfSignals,
    *,
    img_mpix_max: float = IMG_MPIX_MAX,
    page_megapix_max: float = PAGE_MEGAPIX_MAX,
) -> RoutingDecision:
    """Pure gate: Spark by default; Modal when the raster burden is too large to
    be safe on the shared Spark GPU. No I/O, no side effects.

    Offload if *either* signal trips — they catch two distinct failure modes:
    cumulative memory (img_mpix) and a single oversized allocation (page_megapix).
    """
    if signals.img_mpix > img_mpix_max:
        return RoutingDecision(
            backend="modal",
            reason=f"img_mpix {signals.img_mpix:.0f} > {img_mpix_max:.0f}",
            signals=signals,
        )
    if signals.page_megapix > page_megapix_max:
        return RoutingDecision(
            backend="modal",
            reason=f"page_megapix {signals.page_megapix:.1f} > {page_megapix_max:.1f}",
            signals=signals,
        )
    return RoutingDecision(backend="spark", reason="within Spark limits", signals=signals)


def pdf_signals(pdf: Path) -> PdfSignals:
    """Read intrinsic routing signals from a PDF (the only I/O in this module)."""
    info = _run(["pdfinfo", str(pdf)])
    width_pts, height_pts = _page_size_pts(info)
    page_megapix = (width_pts / 72 * RENDER_DPI) * (height_pts / 72 * RENDER_DPI) / 1e6
    return PdfSignals(
        pages=_search_int(info, r"Pages:\s+(\d+)"),
        img_mpix=_embedded_image_megapixels(pdf),
        page_megapix=round(page_megapix, 1),
        size_mb=round(pdf.stat().st_size / 1e6, 1),
    )


def route_pdf(pdf: Path) -> RoutingDecision:
    """Compute signals, then decide. The effectful half plus the pure half."""
    return decide_backend(pdf_signals(pdf))


# -- signal helpers (I/O) --------------------------------------------------

def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout


def _search_int(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


def _page_size_pts(info: str) -> tuple[float, float]:
    match = re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", info)
    return (float(match.group(1)), float(match.group(2))) if match else (0.0, 0.0)


def _embedded_image_megapixels(pdf: Path) -> float:
    """Σ width×height over every embedded image, in millions of pixels."""
    out = _run(["pdfimages", "-list", str(pdf)])
    total = 0
    for line in out.splitlines()[2:]:  # skip the 2-line header
        cols = line.split()
        if len(cols) >= 5 and cols[2] == "image":
            try:
                total += int(cols[3]) * int(cols[4])
            except ValueError:
                pass
    return round(total / 1e6, 1)
