FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/
COPY config/settings.yaml config/settings.yaml
COPY alembic.ini alembic.ini
COPY alembic/ alembic/

# Pre-install CPU-only torch before the main resolve. On Linux x86 `pip
# install torch` defaults to a CUDA wheel that drags in ~5 GB of nvidia-*
# packages we'd never use — librarian's only torch consumer is
# sentence-transformers for BGE embeddings, and the deploy host has no
# GPU. By installing torch+cpu first, the transitive resolution for
# sentence-transformers sees the dep already satisfied.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch

RUN pip install --no-cache-dir -e ".[serve]"

# Bake embedding model into image (~400MB, avoids runtime download)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-base-en-v1.5')"

# --- Runtime stage ---
FROM python:3.12-slim

WORKDIR /app

# Runtime deps only (no build-essential)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq5 wget xz-utils \
        libegl1 libopengl0 libxcb-cursor0 libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

# Install Calibre (headless — ebook-convert + calibredb for format conversion / DRM)
RUN wget -nv -O- https://download.calibre-ebook.com/linux-installer.sh | sh /dev/stdin install_dir=/opt
ENV PATH="/opt/calibre:${PATH}"

# Copy installed Python packages and app from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

# Copy baked embedding model from builder
COPY --from=builder /root/.cache/huggingface /root/.cache/huggingface

EXPOSE 8811

ENV LIBRARIAN_EMBEDDING_DEVICE=cpu

CMD ["sh", "-c", "alembic upgrade head && python -m librarian.mcp_server"]
