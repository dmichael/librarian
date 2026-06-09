"""One-off: invoke the deployed Modal extractor on a tiny PDF to confirm it runs
on the GPU (prints torch device) and returns marker chunks. Reads MODAL auth
from the environment. Usage: python scripts/modal_smoke.py [path-to-pdf]
"""
import sys
from pathlib import Path

import modal

from librarian.cloud_extract import APP_NAME

pdf_path = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/tao_te_ching.pdf")
fn = modal.Function.from_name(APP_NAME, "extract_marker_remote")

with modal.enable_output():  # stream the function's stdout (device line, marker logs) locally
    result = fn.remote(pdf_path.read_bytes(), pdf_path.name)

chunks = result.get("chunks") or {}
print("---")
print("success:", result.get("success"))
print("blocks:", len(chunks.get("blocks", [])))
print("error:", result.get("error"))
