# marker-service

HTTP wrapper around `marker_server` (from upstream `marker-pdf`), packaged for
NVIDIA GPU deployment. Currently targeted at the DGX Spark (GB10 / Blackwell,
arm64); generalize later if a second deploy target appears.

This service is **not coupled to librarian** — it accepts a PDF (or EPUB) upload
and returns marker's chunks JSON. Any consumer can call it.

## Endpoint

`POST /marker` — multipart upload, returns marker chunks JSON. See the
`marker-pdf` README for the exact request/response shape (it's whatever
`marker_server` produces; we don't wrap it).

Default port: `8001`.

## Build

The Spark is arm64. Build from any arm64 host (Apple Silicon Mac works):

```
cd marker-service
docker buildx build --platform linux/arm64 -t marker-service:dev --load .
```

For the registry push, use the Makefile target at the repo root:

```
make push-marker REGISTRY=agents.local:5000
```

## Deploy on the Spark

```
ssh spark.local "cd ~/marker-service && \
  docker compose -f docker-compose.spark.yml pull && \
  docker compose -f docker-compose.spark.yml up -d"
```

## Smoke test

```
curl -F "file=@some.pdf" http://spark.local:8001/marker
```

## Notes

- Base: `nvidia/cuda:12.8.0-runtime-ubuntu24.04` — CUDA 12.8+ is required for
  Blackwell (sm_120/sm_121).
- Model weights (~3GB surya) are baked into the image at build time so the
  first request is fast.
- The container is stateless. Killing/rebuilding loses nothing.
