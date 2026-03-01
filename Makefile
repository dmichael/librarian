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

.PHONY: all status intake extract extract-cloud index clean help build run push deploy deploy-preflight release db-migrate-safe db-migrate-snapshot test-baseline test-retrieval-quality

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
	@echo "  make build        Build Docker image"
	@echo "  make run          Run with docker compose"
	@echo "  make push         Build + push image to registry ($(REGISTRY))"
	@echo "  make deploy-preflight  Check local/remote deploy prerequisites"
	@echo "  make deploy       Build + push + remote compose up on $(DEPLOY_HOST)"
	@echo "  make test-baseline Run local characterization tests (unittest)"
	@echo "  make test-retrieval-quality Run focused retrieval quality smoke tests"
	@echo "  make db-migrate-snapshot  Create safe DB snapshot (no migration)"
	@echo "  make db-migrate-safe      Snapshot + apply Alembic migration safely"
	@echo ""
	@echo "Cloud extraction requires: pip install -e '.[cloud]' && modal setup"

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

# === Container ===
IMAGE ?= librarian
TAG ?= $(shell git describe --always --dirty 2>/dev/null || date +%Y%m%d%H%M%S)
REGISTRY ?= agents.local:5000
DEPLOY_HOST ?= agents.local
DEPLOY_PATH ?= /Users/dmichael/projects/librarian
IMAGE_REF := $(REGISTRY)/$(IMAGE):$(TAG)
REMOTE_SHELL ?= /bin/zsh -lc

build:
	docker build -t $(IMAGE):$(TAG) .
	docker tag $(IMAGE):$(TAG) $(IMAGE_REF)

run:
	docker compose up

test-baseline:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests_baseline -p "test_*.py" -v

test-retrieval-quality:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests_quality -p "test_*.py" -v

# DB migration safety workflow (host-local, no /tmp snapshots)
db-migrate-snapshot:
	@$(PYTHON) scripts/db_safe_migrate.py

db-migrate-safe:
	@$(PYTHON) scripts/db_safe_migrate.py --apply

# Push to the deployment registry
push: build
	docker push $(IMAGE_REF)

deploy-preflight:
	@echo "Preflight: local checks"
	@command -v docker >/dev/null || (echo "ERROR: docker not found"; exit 1)
	@command -v ssh >/dev/null || (echo "ERROR: ssh not found"; exit 1)
	@command -v git >/dev/null || (echo "ERROR: git not found"; exit 1)
	@docker info >/dev/null 2>&1 || (echo "ERROR: docker daemon not reachable"; exit 1)
	@test -f Dockerfile || (echo "ERROR: Dockerfile missing in repo root"; exit 1)
	@test -f docker-compose.prod.yml || (echo "ERROR: docker-compose.prod.yml missing in repo root"; exit 1)
	@echo "Preflight: remote checks on $(DEPLOY_HOST)"
	@ssh -o BatchMode=yes -o ConnectTimeout=10 $(DEPLOY_HOST) "$(REMOTE_SHELL) 'set -e; \
		command -v docker >/dev/null; \
		docker info >/dev/null 2>&1; \
		docker compose version >/dev/null 2>&1; \
		test -d $(DEPLOY_PATH); \
		test -f $(DEPLOY_PATH)/docker-compose.prod.yml; \
		test -f $(DEPLOY_PATH)/.env.librarian'"
	@echo "Preflight passed."

# Release a versioned tag (e.g. make release V=0.1.0 REGISTRY=myregistry:5000)
release:
	@test -n "$(V)" || (echo "Usage: make release V=0.1.0 REGISTRY=myregistry:5000" && exit 1)
	@test -n "$(REGISTRY)" || (echo "Usage: make release V=0.1.0 REGISTRY=myregistry:5000" && exit 1)
	docker tag $(IMAGE):$(TAG) $(REGISTRY)/$(IMAGE):$(V)
	docker push $(REGISTRY)/$(IMAGE):$(V)
	@echo "Pushed $(REGISTRY)/$(IMAGE):$(V)"

deploy: deploy-preflight push
	@echo "Deploying $(IMAGE_REF) to $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "$(REMOTE_SHELL) 'set -e; cd $(DEPLOY_PATH); LIBRARIAN_IMAGE=$(IMAGE_REF) docker compose -f docker-compose.prod.yml up -d --remove-orphans; docker compose -f docker-compose.prod.yml ps'"
