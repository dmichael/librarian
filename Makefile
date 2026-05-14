# Librarian Pipeline Makefile
#
# Usage:
#   make              - Run full pipeline (intake → extract → index)
#   make status       - Show pipeline state
#   make extract      - Extract books locally (GPU-intensive, hours per book)
#   make extract-cloud - Extract books on Modal A100s (parallel, ~$3-5/book)
#   make index        - Index extracted books to vector store

VENV := .venv/bin
PYTHON ?= $(if $(wildcard $(VENV)/python),$(VENV)/python,python3)

.PHONY: all status intake extract extract-cloud index clean help build run deploy preflight release db-migrate-safe db-migrate-snapshot test-baseline test-retrieval-quality

# Default: run full pipeline
all: intake extract index

help:
	@echo "Librarian Pipeline"
	@echo ""
	@echo "Usage:"
	@echo "  make              Run full pipeline (intake → extract → index)"
	@echo "  make status       Show pipeline state from Calibre"
	@echo "  make intake       Import new books to Calibre"
	@echo "  make extract      Extract books locally (slow, ~10h/book)"
	@echo "  make extract-cloud Extract on Modal A100s (fast, parallel)"
	@echo "  make index        Index extracted content to vector store"
	@echo "  git push ms01 main  Publish source to bare repo on $(BUILD_SSH)"
	@echo "  make build         Build amd64 image on $(BUILD_SSH) from ms01/main, push to $(REGISTRY)"
	@echo "  make deploy        Pull image on $(DEPLOY_CONTEXT) + compose up -d"
	@echo "  make preflight     Bootstrap git remote / build workspace / docker context"
	@echo "  make run           Run locally with docker compose (dev)"
	@echo "  make test-baseline Run local characterization tests (unittest)"
	@echo "  make test-retrieval-quality Run focused retrieval quality smoke tests"
	@echo "  make db-migrate-snapshot  Create safe DB snapshot (no migration)"
	@echo "  make db-migrate-safe      Snapshot + apply Alembic migration safely"
	@echo ""
	@echo "Cloud extraction requires: pip install -e '.[cloud]' && modal setup"
	@echo "Docker API override: DOCKER_API_VERSION=1.45 (set higher/lower per host if needed)"

# Show pipeline status
status:
	@$(VENV)/librarian-status

# Import new books to Calibre
intake:
	@$(VENV)/librarian-intake

# Extract books locally (serialized, uses local GPU)
extract:
	@$(VENV)/librarian-extract

# Extract books on cloud (parallel Modal A100s)
# Requires: pip install -e ".[cloud]" && modal setup
extract-cloud:
	@$(VENV)/librarian-extract --cloud

# Dry-run cloud extraction (see what would be extracted)
extract-cloud-dry:
	@$(VENV)/librarian-extract --cloud --dry-run

# Index extracted content
index:
	@$(VENV)/librarian-index

# Run full pipeline on cloud
cloud: intake extract-cloud index

# Clean extracted content (use with caution)
clean-extracted:
	@echo "This would remove all extracted content. Use 'make clean-extracted-confirm' to proceed."

clean-extracted-confirm:
	rm -rf ~/data/librarian/converted/*

# === Container / deploy ===
#
# Three independent operations, same shape as marker-service:
#   git push ms01 main      — publish source to bare repo on ms-01
#   make build              — ms-01 builds linux/amd64 image, pushes to ms-01.local:5000
#   make deploy             — ms-01 pulls image, compose up -d
#
# build host == deploy host here (both ms-01), so the registry roundtrip
# is local; pull is fast.

IMAGE ?= librarian
TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
REGISTRY ?= ms-01.local:5000
IMAGE_REF := $(REGISTRY)/$(IMAGE):$(TAG)
IMAGE_LATEST := $(REGISTRY)/$(IMAGE):latest
PLATFORM ?= linux/amd64

DOCKER ?= docker
DOCKER_API_VERSION ?= 1.45
DOCKER_API_ENV := DOCKER_API_VERSION=$(DOCKER_API_VERSION)

# Build host: where the bare git repo lives and the docker build runs.
BUILD_SSH    ?= dmichael@ms-01.local
GIT_REMOTE   ?= ms01
GIT_BARE     ?= /srv/git/librarian.git
BUILD_WORK   ?= /srv/librarian/build
GIT_REMOTE_URL ?= $(BUILD_SSH):$(GIT_BARE)

# Deploy host: pulls the image from the registry and runs the container.
# For librarian this is the same host as the build, but kept as a distinct
# var so the role split stays explicit (and can change later).
DEPLOY_CONTEXT ?= ms01
DEPLOY_SSH     ?= dmichael@ms-01.local

# Bind-mount target on the deploy host. /srv/<service>/data follows the
# Spark convention; ansible's docker_host role chowns /srv to dmichael so
# the build workspace can mkdir freely.
DATA_DIR ?= /srv/librarian/data

run:
	$(DOCKER_API_ENV) $(DOCKER) compose up

test-baseline:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests_baseline -p "test_*.py" -v

test-retrieval-quality:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests_quality -p "test_*.py" -v

# DB migration safety workflow (host-local, no /tmp snapshots)
db-migrate-snapshot:
	@$(PYTHON) scripts/db_safe_migrate.py

db-migrate-safe:
	@$(PYTHON) scripts/db_safe_migrate.py --apply

preflight:
	@echo "Preflight: local"
	@command -v docker >/dev/null || (echo "ERROR: docker not found"; exit 1)
	@command -v git >/dev/null || (echo "ERROR: git not found"; exit 1)
	@test -f Dockerfile || (echo "ERROR: Dockerfile missing"; exit 1)
	@test -f docker-compose.prod.yml || (echo "ERROR: docker-compose.prod.yml missing"; exit 1)
	@test -f .env.production || (echo "ERROR: .env.production missing — create one (gitignored) with deploy env vars"; exit 1)
	@git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
		|| (echo "ERROR: not inside a git repo"; exit 1)
	@echo "Preflight: bare git repo at $(BUILD_SSH):$(GIT_BARE)"
	@ssh -o StrictHostKeyChecking=accept-new $(BUILD_SSH) "test -d $(GIT_BARE) || ( \
		mkdir -p $$(dirname $(GIT_BARE)) && \
		git init --bare -q $(GIT_BARE) \
	)"
	@echo "Preflight: local git remote '$(GIT_REMOTE)'"
	@git remote get-url $(GIT_REMOTE) >/dev/null 2>&1 \
		|| git remote add $(GIT_REMOTE) $(GIT_REMOTE_URL)
	@echo "Preflight: docker context '$(DEPLOY_CONTEXT)'"
	@$(DOCKER) context inspect $(DEPLOY_CONTEXT) >/dev/null 2>&1 \
		|| $(DOCKER) context create $(DEPLOY_CONTEXT) --docker host=ssh://$(DEPLOY_SSH) >/dev/null
	@$(DOCKER_API_ENV) $(DOCKER) --context $(DEPLOY_CONTEXT) info >/dev/null 2>&1 \
		|| (echo "ERROR: cannot reach remote docker via context '$(DEPLOY_CONTEXT)'"; exit 1)
	@echo "Preflight: data dir on $(DEPLOY_SSH):$(DATA_DIR)"
	@ssh -o StrictHostKeyChecking=accept-new $(DEPLOY_SSH) "mkdir -p $(DATA_DIR)"

build: preflight
	@# Build operates on what's been pushed to $(GIT_REMOTE)/main, not on
	@# the local working tree. If you forgot to push, error out with a hint
	@# instead of silently building a stale image.
	@git fetch -q $(GIT_REMOTE) main 2>/dev/null || true
	@local_sha=$$(git rev-parse HEAD); \
	 remote_sha=$$(git rev-parse $(GIT_REMOTE)/main 2>/dev/null || echo ""); \
	 if [ -z "$$remote_sha" ]; then \
	   echo "ERROR: $(GIT_REMOTE)/main not found. Push first: git push $(GIT_REMOTE) main"; \
	   exit 1; \
	 fi; \
	 if [ "$$local_sha" != "$$remote_sha" ]; then \
	   echo "ERROR: local HEAD ($$local_sha) differs from $(GIT_REMOTE)/main ($$remote_sha)."; \
	   echo "       Push first: git push $(GIT_REMOTE) main"; \
	   exit 1; \
	 fi
	@echo "Building on $(BUILD_SSH) (native amd64) and pushing image to $(REGISTRY)..."
	ssh -o StrictHostKeyChecking=accept-new $(BUILD_SSH) "set -e; \
		if [ ! -d $(BUILD_WORK)/.git ]; then \
			mkdir -p $$(dirname $(BUILD_WORK)) && \
			git clone -q $(GIT_BARE) $(BUILD_WORK); \
		fi; \
		cd $(BUILD_WORK) && \
		git fetch -q origin main && \
		git reset --hard -q origin/main && \
		docker buildx build \
			--platform $(PLATFORM) \
			-t $(IMAGE_REF) \
			-t $(IMAGE_LATEST) \
			--push ."
	@echo "Built and pushed $(IMAGE_REF) (also tagged :latest)."

# Release a versioned tag (e.g. make release V=0.1.0)
release:
	@test -n "$(V)" || (echo "Usage: make release V=0.1.0" && exit 1)
	$(DOCKER_API_ENV) $(DOCKER) --context $(DEPLOY_CONTEXT) pull $(IMAGE_LATEST)
	$(DOCKER_API_ENV) $(DOCKER) --context $(DEPLOY_CONTEXT) tag $(IMAGE_LATEST) $(REGISTRY)/$(IMAGE):$(V)
	$(DOCKER_API_ENV) $(DOCKER) --context $(DEPLOY_CONTEXT) push $(REGISTRY)/$(IMAGE):$(V)
	@echo "Pushed $(REGISTRY)/$(IMAGE):$(V)"

deploy: preflight
	@echo "Pulling latest librarian image on $(DEPLOY_CONTEXT)..."
	LIBRARIAN_DATA_DIR=$(DATA_DIR) $(DOCKER_API_ENV) \
		$(DOCKER) --context $(DEPLOY_CONTEXT) compose \
			-f docker-compose.prod.yml pull
	@echo "Running compose up -d..."
	LIBRARIAN_DATA_DIR=$(DATA_DIR) $(DOCKER_API_ENV) \
		$(DOCKER) --context $(DEPLOY_CONTEXT) compose \
			-f docker-compose.prod.yml up -d --remove-orphans
	$(DOCKER) --context $(DEPLOY_CONTEXT) compose \
		-f docker-compose.prod.yml ps
