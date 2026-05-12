# marker-service: marker-pdf behind an HTTP API, targeted at NVIDIA GPUs.
#
# Targeted at DGX Spark (GB10 / Blackwell, arm64) — CUDA 12.8 is required
# for sm_120/sm_121. Build with: docker buildx build --platform linux/arm64 ...
#
# This image is intentionally generic: it has no librarian-specific code.
# It exposes `marker_server` from upstream marker-pdf on port 8001.

FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-venv \
        python3-pip \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# marker-pdf pulls torch, surya-ocr, etc.
# weasyprint + ebooklib enable EPUB input via marker's epub→pdf path.
RUN pip install \
        "marker-pdf>=1.0.0" \
        "weasyprint>=60.0" \
        "ebooklib>=0.18" \
        "uvicorn[standard]" \
        "fastapi" \
        "python-multipart" \
        reportlab

# Pre-download surya / marker model weights so the first request doesn't
# pay for a ~3GB download. Same trick the Modal image uses.
RUN python -c "\
from reportlab.lib.pagesizes import letter; \
from reportlab.pdfgen import canvas; \
c = canvas.Canvas('/tmp/test.pdf', pagesize=letter); \
c.drawString(72, 720, 'test'); c.save()" \
 && marker_single /tmp/test.pdf --output_dir /tmp/marker_out --output_format chunks \
 && rm -rf /tmp/test.pdf /tmp/marker_out

EXPOSE 8001

# marker_server ships inside marker-pdf. POST /marker accepts a file upload.
CMD ["marker_server", "--host", "0.0.0.0", "--port", "8001"]
