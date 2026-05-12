# marker-service: marker-pdf behind an HTTP API, targeted at the DGX Spark.
#
# Base: NVIDIA NGC PyTorch container. Starting from 25.10-py3 these are
# verified working on GB10 (Blackwell, sm_121) with bundled CUDA 13 /
# cuBLAS that covers Blackwell matmul shapes — including the ones surya's
# table_rec decoder hits, which fail on stock CUDA 12.8 cuBLAS with
# CUBLAS_STATUS_INTERNAL_ERROR. Matching the 25.11 release used by the
# vLLM image already deployed on this host.
#
# Build for linux/arm64 (Spark is arm64):
#   docker buildx build --platform linux/arm64 --load ...
#
# This image is intentionally generic — no librarian-specific code. It
# exposes `marker_server` from upstream marker-pdf on port 8001.

FROM nvcr.io/nvidia/pytorch:25.11-py3

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# marker-pdf needs poppler for PDF handling and weasyprint's system deps
# (pango/harfbuzz) for its EPUB->PDF path. NGC pytorch already ships most
# build tooling; we add only what marker actually needs at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*

# Install marker-pdf and its companions into NGC's system Python.
# --upgrade-strategy=only-if-needed protects NGC's torch build from being
# replaced by a stable PyPI wheel — pip will leave already-satisfying
# packages alone. NGC ships torch 2.9.x which satisfies marker-pdf's
# torch>=2.0 requirement, so torch won't be touched.
RUN pip install --upgrade-strategy=only-if-needed \
        "marker-pdf>=1.0.0" \
        "weasyprint>=60.0" \
        "ebooklib>=0.18" \
        "uvicorn[standard]" \
        "fastapi" \
        "python-multipart" \
        "reportlab"

# Pre-download surya/marker model weights so the first request doesn't
# pay for a ~3GB download. The CMD entry point (marker_server) imports
# fastapi routes at module load — running marker_single here exercises
# the package but not the server's route decoration, so we also smoke
# the server's import below.
RUN python -c "\
from reportlab.lib.pagesizes import letter; \
from reportlab.pdfgen import canvas; \
c = canvas.Canvas('/tmp/test.pdf', pagesize=letter); \
c.drawString(72, 720, 'test'); c.save()" \
 && marker_single /tmp/test.pdf --output_dir /tmp/marker_out --output_format chunks \
 && rm -rf /tmp/test.pdf /tmp/marker_out

# Build-time smoke test of the production entry point. Catches
# import-time decorator failures (missing python-multipart, route
# conflicts, schema errors) that marker_single doesn't exercise.
RUN marker_server --help >/dev/null

EXPOSE 8001

CMD ["marker_server", "--host", "0.0.0.0", "--port", "8001"]
