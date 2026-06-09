"""Modal backend for PDF extraction — the offload target for documents too
large for the shared Spark GPU (see extract_routing.decide_backend).

Deploy once, pay per use. `modal deploy src/librarian/cloud_extract.py` builds
the image (marker models baked in, downloaded once) and registers the function;
scale-to-zero means there is no idle cost between runs. The extract path then
calls the deployed function on demand via Function.from_name(...).remote() — GPU
spins up, runs marker, returns, spins down. 99% of ingestion stays on the free
local Spark; only the rare oversized scan touches Modal.

The function returns marker's native chunks ({blocks, page_info}) plus metadata —
the same structures marker_server returns — so an offloaded document yields a
document.json byte-compatible with a Spark-extracted one. librarian writes the
artifacts and derives document.md / equations.json locally (see marker.py).

Requires Modal auth (MODAL_TOKEN_ID / MODAL_TOKEN_SECRET) and the optional
[cloud] extra (modal) installed in whatever process calls run_modal_extraction.
"""
from __future__ import annotations

from pathlib import Path

import modal

APP_NAME = "librarian-extract"

# ~50 GB peak was observed extracting the 253 MB book on the Spark; 80 GB gives
# real headroom. A100-80GB syntax per Modal 1.x GPU guide.
GPU = "A100-80GB"

# Large scans run ~30 min even on a fast GPU; allow margin.
EXTRACT_TIMEOUT = 3600

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("poppler-utils", "libgl1", "libglib2.0-0")
    .pip_install("marker-pdf>=1.0.0", "reportlab")
    # Bake surya/marker weights into the image so each ephemeral run starts warm
    # instead of re-downloading ~3 GB. Triggering a real marker_single run is the
    # documented-by-behavior way to populate the model cache (same as upstream).
    .run_commands(
        "python -c \"from reportlab.pdfgen import canvas; c = canvas.Canvas('/tmp/warm.pdf'); c.drawString(72, 720, 'warm'); c.save()\"",
        "marker_single /tmp/warm.pdf --output_dir /tmp/warm_out --output_format chunks || true",
        "rm -rf /tmp/warm.pdf /tmp/warm_out",
    )
)


@app.function(gpu=GPU, image=image, timeout=EXTRACT_TIMEOUT, retries=1)
def extract_marker_remote(pdf_bytes: bytes, filename: str) -> dict:
    """Run marker on one PDF inside Modal and return its chunks + metadata.

    Returns {"success": bool, "chunks": dict | None, "metadata": dict | None,
             "error": str | None}. `chunks` is marker's {blocks, page_info}.
    """
    import json
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # Preserve the suffix so marker detects the format.
        src = tmp_dir / (Path(filename).name or "input.pdf")
        src.write_bytes(pdf_bytes)
        out_dir = tmp_dir / "out"
        out_dir.mkdir()

        print(f"[modal] marker_single: {src.name} ({len(pdf_bytes) / 1e6:.0f} MB)", flush=True)
        proc = subprocess.run(
            ["marker_single", str(src),
             "--output_dir", str(out_dir), "--output_format", "chunks"],
            text=True,
        )
        if proc.returncode != 0:
            return {"success": False, "chunks": None, "metadata": None,
                    "error": f"marker_single exited {proc.returncode}"}

        # marker writes out/<name>/<name>.json + <name>_meta.json
        chunks_file = meta_file = None
        for jf in out_dir.rglob("*.json"):
            if jf.name.endswith("_meta.json"):
                meta_file = jf
            else:
                chunks_file = jf
        if chunks_file is None:
            return {"success": False, "chunks": None, "metadata": None,
                    "error": "marker produced no chunks JSON"}

        return {
            "success": True,
            "chunks": json.loads(chunks_file.read_text()),
            "metadata": json.loads(meta_file.read_text()) if meta_file else {},
            "error": None,
        }


def run_modal_extraction(source: Path, filename: str | None = None) -> dict:
    """Invoke the DEPLOYED Modal function for one PDF, returning its result dict.

    Calls the function registered by `modal deploy` (see module docstring) via
    Function.from_name — the image is already built and marker models already
    baked, so there's no per-call build or download, and scale-to-zero means no
    idle cost between calls. Auth via MODAL_TOKEN_ID / MODAL_TOKEN_SECRET.

    The PDF is passed as bytes; Modal blob-uploads large arguments transparently.
    If a very large file ever fails here, switch to a Modal Volume. (Confirm on
    the first big run.)
    """
    source = Path(source)
    fn = modal.Function.from_name(APP_NAME, "extract_marker_remote")
    return fn.remote(source.read_bytes(), filename or source.name)
