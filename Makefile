# Librarian Pipeline - Make-native implementation
#
# Usage:
#   make              Run full pipeline
#   make -j4          Run with 4 parallel jobs
#   make status       Show pipeline status
#   make extract      Extract all pending books
#   make clean        Remove state markers (not converted files)

SHELL := /bin/bash

# === Paths ===
DATA      := $(HOME)/data/librarian
INTAKE    := $(DATA)/intake/ebooks
KINDLE    := $(DATA)/intake/kindle
STATE     := $(DATA)/state
CONVERTED := $(DATA)/converted
LIBRARY   := $(DATA)/calibre
VENV      := $(CURDIR)/.venv/bin

# === Discover inputs at parse time ===
INTAKE_FILES := $(wildcard $(INTAKE)/*.pdf) $(wildcard $(INTAKE)/*.epub)
BOOK_IDS     := $(shell calibredb list --library-path $(LIBRARY) --for-machine 2>/dev/null | jq -r '.[].id')

# === Derive targets from inputs ===
# Extracted markdown: book ID 105 → converted/105/full.md
EXTRACTED := $(patsubst %,$(CONVERTED)/%/full.md,$(BOOK_IDS))

# Indexed markers: book ID 105 → state/indexed/105.done
INDEXED := $(patsubst %,$(STATE)/indexed/%.done,$(BOOK_IDS))

# === Default target ===
.PHONY: all
all: ingest $(INDEXED)

# === Stage 1: Ingest PDFs/EPUBs → Calibre ===
# Uses script because Make can't handle spaces in filenames
.PHONY: ingest
ingest: | $(STATE)/ingested $(STATE)/failed
	@for f in "$(INTAKE)"/*.pdf "$(INTAKE)"/*.epub; do \
		[ -f "$$f" ] || continue; \
		base=$$(basename "$$f"); \
		hash=$$(shasum -a 256 "$$f" | cut -c1-16); \
		marker="$(STATE)/ingested/$$hash.id"; \
		[ -f "$$marker" ] && continue; \
		echo "Ingesting: $$base"; \
		output=$$(calibredb add --library-path $(LIBRARY) --automerge ignore "$$f" 2>&1); \
		if echo "$$output" | grep -q "Added book ids:"; then \
			echo "$$output" | grep "Added book ids:" | grep -oE '[0-9]+' | head -1 > "$$marker"; \
			echo "  → Calibre ID $$(cat $$marker)"; \
		elif echo "$$output" | grep -q "already exist"; then \
			echo "exists" > "$$marker"; \
			echo "  → Already in Calibre"; \
		else \
			echo "$$output" > "$(STATE)/failed/$$hash.failed"; \
			echo "  → Failed"; \
		fi; \
	done

# === Stage 2: Extract Calibre books → Markdown ===
# The target IS the actual output file - pure Make

$(CONVERTED)/%/full.md:
	@echo "Extracting: ID $*"
	@$(VENV)/librarian-extract --book-id $*

# === Stage 3: Index Markdown → Vector store ===
# Depends on extraction completing first

$(STATE)/indexed/%.done: $(CONVERTED)/%/full.md | $(STATE)/indexed
	@echo "Indexing: ID $*"
	@$(VENV)/librarian-index --book-id $* 2>/dev/null && touch "$@" || touch "$@"

# === Kindle (delegates to existing Python - complex DRM logic) ===
.PHONY: kindle
kindle:
	@$(VENV)/librarian-kindle-extract 2>&1 | sed 's/^/  /'

# === Directory creation (order-only prerequisites) ===
$(STATE)/ingested $(STATE)/indexed $(STATE)/failed:
	@mkdir -p $@

# === Utility targets ===
.PHONY: status extract index clean clear-lock help

status:
	@echo "Librarian Pipeline Status"
	@echo "========================="
	@echo "Intake files:    $(words $(INTAKE_FILES))"
	@echo "Books in Calibre: $(words $(BOOK_IDS))"
	@echo "Ingested:        $$(ls -1 $(STATE)/ingested/*.id 2>/dev/null | wc -l | tr -d ' ')"
	@echo "Extracted:       $$(ls -1 $(CONVERTED)/*/full.md 2>/dev/null | wc -l | tr -d ' ')"
	@echo "Indexed:         $$(ls -1 $(STATE)/indexed/*.done 2>/dev/null | wc -l | tr -d ' ')"
	@echo "Failed:          $$(ls -1 $(STATE)/failed/* 2>/dev/null | wc -l | tr -d ' ')"

# Explicit stage targets (for manual use)
extract: $(EXTRACTED)
index: $(INDEXED)

clean:
	rm -f $(STATE)/ingested/*.id $(STATE)/indexed/*.done $(STATE)/failed/*

# Clear stuck locks (if processes hung and were killed)
clear-lock:
	@for lock in /tmp/librarian-marker.lock /tmp/librarian-qdrant.lock; do \
		if [ -f "$$lock" ]; then \
			echo "Clearing $$lock"; \
			rm -f "$$lock"; \
		fi; \
	done
	@echo "Locks cleared"

help:
	@echo "Librarian Pipeline (Make-native)"
	@echo ""
	@echo "Usage:"
	@echo "  make            Run full pipeline (ingest → extract → index)"
	@echo "  make -j4        Run with 4 parallel extraction/indexing jobs"
	@echo "  make status     Show current pipeline state"
	@echo "  make kindle     Process Kindle books (DRM removal)"
	@echo "  make extract    Extract all pending books to markdown"
	@echo "  make clean      Clear state markers (allows re-run)"
	@echo "  make clear-lock Clear stuck marker lock (if killed mid-extract)"
	@echo ""
	@echo "Current state:"
	@echo "  $(words $(INTAKE_FILES)) intake files"
	@echo "  $(words $(BOOK_IDS)) books in Calibre"
