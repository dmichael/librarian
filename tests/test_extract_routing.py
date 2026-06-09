"""Tests for extract_routing — the pure Spark-vs-Modal gate.

decide_backend takes signals and returns a decision with no I/O, so these run
with no PDFs and no infrastructure. Cases mirror the real calibration points.
"""
from librarian.extract_routing import (
    IMG_MPIX_MAX,
    PAGE_MEGAPIX_MAX,
    PdfSignals,
    decide_backend,
)


def _sig(img_mpix=0.0, page_megapix=2.0, pages=100, size_mb=5.0):
    return PdfSignals(
        pages=pages, img_mpix=img_mpix, page_megapix=page_megapix, size_mb=size_mb,
    )


def test_light_pdf_stays_on_spark():
    # DDIA-like: 613 pages but born-digital, low raster.
    assert decide_backend(_sig(img_mpix=444, page_megapix=2.4)).backend == "spark"


def test_high_total_raster_offloads():
    # Cracking-like: cumulative image burden over the line.
    d = decide_backend(_sig(img_mpix=1091, page_megapix=2.5))
    assert d.backend == "modal"
    assert "img_mpix" in d.reason


def test_oversized_page_offloads_even_when_total_is_low():
    # fund-like single-allocation risk: huge per-page raster, modest total.
    d = decide_backend(_sig(img_mpix=100, page_megapix=13.2))
    assert d.backend == "modal"
    assert "page_megapix" in d.reason


def test_at_threshold_stays_on_spark():
    # Strictly-greater triggers offload, so exactly at the limit is still Spark.
    d = decide_backend(_sig(img_mpix=IMG_MPIX_MAX, page_megapix=PAGE_MEGAPIX_MAX))
    assert d.backend == "spark"


def test_decision_is_deterministic():
    s = _sig(img_mpix=1200)
    assert decide_backend(s) == decide_backend(s)


def test_custom_thresholds_are_honored():
    s = _sig(img_mpix=500)
    assert decide_backend(s).backend == "spark"
    assert decide_backend(s, img_mpix_max=400).backend == "modal"


def test_extract_path_imports_cleanly():
    # Guards the wire-in: extract.py importing route_pdf must not break.
    import librarian.extract  # noqa: F401
