# Librarian Pipeline Makefile
#
# Usage:
#   make              - Run full pipeline (intake → extract → index)
#   make status       - Show pipeline state
#   make extract      - Extract books locally (GPU-intensive, hours per book)
#   make extract-cloud - Extract books on Modal A100s (parallel, ~$3-5/book)
#   make index        - Index extracted books to vector store

VENV := .venv/bin

.PHONY: all status intake extract extract-cloud index clean help

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
