# Spec: Pipeline State Unification

**Date**: 2026-01-18
**Status**: Draft
**Predecessor**: `specs/pipeline-cleanup` (partially implemented)

---

## Problem Statement

The pipeline has **three sources of truth per stage**, making it hard to reason about state and debug failures:

| Stage | Source 1 | Source 2 | Source 3 |
|-------|----------|----------|----------|
| Ingest | `state/ingested/*.id` (Makefile) | `state/pipeline.json` (state.py) | — |
| Extract | `converted/{id}/full.md` existence | Calibre `*source_hash` column | `state/pipeline.json` |
| Index | `state/indexed/*.done` (Makefile) | VectorStore `get_indexed_ids()` | — |

This fragmentation causes:

1. **Sync issues**: Delete `converted/105/full.md` but Calibre still thinks it's extracted
2. **Orphaned code**: `state.py` exists but Makefile doesn't use it
3. **Hidden failures**: Index target does `|| touch "$@"` masking errors
4. **Debugging confusion**: Which marker should I trust?

Additionally:
- Kindle extraction is not part of `make all`
- `BOOK_IDS` re-queries Calibre on every `make` invocation (slow)
- `state/extracted/` directory exists but is never used

---

## Current State

### What Was Implemented (from pipeline-cleanup spec)

- Directory structure (`intake/`, `staging/`, `state/`, `converted/`)
- `state.py` with `PipelineState` class
- `drm_diagnosis.py` for DRM failure analysis
- Makefile-native orchestration with pattern rules
- `make pending` and `make ingest-one` targets (added today)

### What's Still Broken

1. **state.py is orphaned**: Created but only used by `librarian-status`, not Makefile
2. **Calibre metadata vs file markers**: Extract checks both, can disagree
3. **Index always succeeds**: `|| touch "$@"` hides failures
4. **Kindle not in main flow**: Must run `make kindle` separately

---

## Requirements

### Must Have

- [ ] Single source of truth per stage (pick one, remove others)
- [ ] Index target fails hard on error (remove `|| touch "$@"`)
- [ ] Remove or integrate `state.py` / `pipeline.json`
- [ ] Delete orphaned `state/extracted/` directory

### Should Have

- [ ] Kindle in default pipeline (`make all` includes Kindle if files exist)
- [ ] Cache `BOOK_IDS` to avoid Calibre query on every `make` invocation
- [ ] Document idempotency model in AGENTS.md

### Nice to Have

- [ ] `make verify` target to check state consistency
- [ ] Failed extraction/indexing tracked in `state/failed/`
- [ ] Retry mechanism for transient failures

---

## Proposed Design

### Single Source of Truth

| Stage | Canonical Source | Remove |
|-------|------------------|--------|
| Ingest | `state/ingested/{hash}.id` | `pipeline.json` tracking |
| Extract | `converted/{id}/full.md` existence | Calibre `*source_hash` (or make it cache only) |
| Index | `state/indexed/{id}.done` | VectorStore query (or make it verification only) |

### Fix Index Target

```makefile
# Before (hides errors)
$(STATE)/indexed/%.done: $(CONVERTED)/%/full.md | $(STATE)/indexed
    @$(VENV)/librarian-index --book-id $* 2>/dev/null && touch "$@" || touch "$@"

# After (fails on error)
$(STATE)/indexed/%.done: $(CONVERTED)/%/full.md | $(STATE)/indexed
    @$(VENV)/librarian-index --book-id $*
    @touch "$@"
```

### Integrate Kindle

```makefile
# Check if Kindle files exist, run kindle target first
KINDLE_FILES := $(wildcard $(KINDLE)/*/*.azw* $(KINDLE)/*/*.kfx)

all: kindle-if-needed ingest $(INDEXED)

kindle-if-needed:
    @if [ -n "$(KINDLE_FILES)" ]; then $(MAKE) kindle; fi
```

### Remove state.py or Commit to It

**Option A: Remove** (recommended)
- Delete `src/librarian/state.py`
- Delete `state/pipeline.json`
- Update `librarian-status` to read from file markers only

**Option B: Commit**
- Make Makefile write to `pipeline.json` via helper script
- Remove file markers, use JSON only
- More complex, less Make-idiomatic

---

## Success Criteria

- [ ] `make status` output matches actual file system state
- [ ] Deleting `converted/105/full.md` and re-running `make` re-extracts book 105
- [ ] Failed indexing causes `make` to exit non-zero
- [ ] No references to `state.py` or `pipeline.json` in Makefile
- [ ] AGENTS.md documents which marker = which stage

---

## Out of Scope

- Vector store migration between backends
- Equation/chapter collection state tracking (separate spec)
- Classification stage automation

---

## Open Questions

1. Should Calibre custom columns (`*source_hash`, `extraction_date`) be removed entirely or kept as optional metadata?
2. Is `state/failed/` worth implementing for all stages, or just leave failures as "no marker"?
3. Should `make verify` be a blocking prerequisite or advisory-only?

---

## Files to Modify

| File | Changes |
|------|---------|
| `Makefile` | Fix index target, add kindle-if-needed, cache BOOK_IDS |
| `src/librarian/state.py` | Delete or refactor |
| `src/librarian/extract.py` | Remove Calibre source_hash check (use file existence only) |
| `AGENTS.md` | Document idempotency model |
| `state/extracted/` | Delete directory |
| `state/pipeline.json` | Delete file |
