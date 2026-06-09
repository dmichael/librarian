#!/usr/bin/env python3
"""Gather cheap pre-flight signals for every source PDF and line them up against
the known Spark extraction outcomes, to find what actually separates the books
that must be offloaded (to Modal) from the ones the Spark handles fine.

Read-only: runs pdfinfo / pdffonts over the local Calibre source mirror.
No GPU, no extraction. Just measures the predictors we'd gate on.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

CALIBRE = Path.home() / "data/librarian/calibre"

# Marker renders pages to bitmaps at a fixed target DPI regardless of source.
# The absolute value only scales V uniformly; it's the *relative* ordering that
# matters for finding a threshold. 192 is marker/surya's common highres target.
RENDER_DPI = 192

# Ground truth from today's backfill:
#   "offload" = failed on the Spark at default batch sizes (needed special handling)
#   "spark_ok" = extracted successfully on the Spark at default settings
OUTCOME = {
    21: "OFFLOAD",   # Cracking 88MB scan — cuBLAS/OOM at default batches
    32: "OFFLOAD",   # fund-industry 253MB scan — OOM
    1: "spark_ok", 3: "spark_ok", 5: "spark_ok", 7: "spark_ok",
    19: "spark_ok", 20: "spark_ok", 22: "spark_ok", 26: "spark_ok",
    27: "spark_ok", 28: "spark_ok", 29: "spark_ok", 31: "spark_ok",
}


def run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return ""


def book_id_from_path(p: Path) -> int | None:
    # Calibre dir names end with " (<id>)"
    for part in p.parts:
        m = re.search(r"\((\d+)\)\s*$", part)
        if m:
            return int(m.group(1))
    return None


def pdf_signals(pdf: Path) -> dict:
    info = run(["pdfinfo", str(pdf)])
    pages = _grep_int(info, r"Pages:\s+(\d+)")
    pw, ph = _page_size_pts(info)
    # Real raster burden: sum of embedded image pixels (what marker must OCR).
    # Far more reliable than font-detection, which misses scans that carry an
    # OCR text layer (e.g. Cracking: 712 full-page JPEGs *and* fonts).
    img_mpix = _embedded_image_megapixels(pdf)
    size_mb = pdf.stat().st_size / 1e6
    img_per_page = img_mpix / pages if pages else 0
    # image-heavy if each page carries a substantial raster (scan-like)
    image_heavy = img_per_page >= 0.5
    return {
        "pages": pages or 0,
        "page_megapix": round((pw / 72 * RENDER_DPI) * (ph / 72 * RENDER_DPI) / 1e6, 1) if pw and ph else 0,
        "img_mpix": round(img_mpix),
        "img_per_pg": round(img_per_page, 2),
        "image_heavy": image_heavy,
        "size_mb": round(size_mb, 1),
    }


def _embedded_image_megapixels(pdf: Path) -> float:
    out = run(["pdfimages", "-list", str(pdf)])
    total = 0.0
    for line in out.splitlines()[2:]:
        cols = line.split()
        if len(cols) >= 5 and cols[2] == "image":
            try:
                total += int(cols[3]) * int(cols[4])
            except ValueError:
                pass
    return total / 1e6


def _grep_int(text: str, pat: str) -> int | None:
    m = re.search(pat, text)
    return int(m.group(1)) if m else None


def _page_size_pts(info: str) -> tuple[float, float]:
    m = re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", info)
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def main() -> None:
    rows = []
    for pdf in CALIBRE.rglob("*.pdf"):
        bid = book_id_from_path(pdf)
        if bid is None:
            continue
        sig = pdf_signals(pdf)
        sig["id"] = bid
        sig["outcome"] = OUTCOME.get(bid, "-")
        rows.append(sig)

    # sort by the real raster-burden signal so the separating line is visible
    rows.sort(key=lambda r: r["img_mpix"])

    hdr = f"{'id':>4} {'outcome':>8} {'MB':>7} {'pages':>6} {'img_mpix':>9} {'img/pg':>7} {'heavy':>6}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['id']:>4} {r['outcome']:>8} {r['size_mb']:>7} {r['pages']:>6} "
              f"{r['img_mpix']:>9} {r['img_per_pg']:>7} {'Y' if r['image_heavy'] else 'n':>6}")

    print("\nOFFLOAD vs spark_ok separation on each signal:")
    for key in ("size_mb", "pages", "img_mpix", "img_per_pg"):
        off = [r[key] for r in rows if r["outcome"] == "OFFLOAD"]
        ok = [r[key] for r in rows if r["outcome"] == "spark_ok"]
        if off and ok:
            print(f"  {key:>8}: spark_ok max={max(ok):>8}   OFFLOAD min={min(off):>8}   "
                  f"{'CLEAN GAP' if min(off) > max(ok) else 'OVERLAP'}")


if __name__ == "__main__":
    main()
