# Refactor Plan: Multi-Extractor, Per-Domain Extraction

This plan walks from the current state (Marker-privileged, manual GROBID, manual reconcile, single-stream RAG) to the spec (extractors equal, per-domain builders, per-domain RAG) in independently shippable steps. Each step is small enough to do in one sitting and reversible if it goes sideways.

The ordering goes: low-risk renames first, then wiring, then RAG. RAG behavior changes are last so existing queries keep working until the new surfaces are proven.

## Step 1 — Rename marker-specific helpers

**Change:** In `files.py`, rename `find_markdown` → `marker_markdown` and `find_content_json` → `marker_content_json`. Update all call sites. Same for `find_meta_json` / `find_html`.

**Why:** The names lie. They claim to "find markdown" but only look under `raw/marker/`. Fixing the names makes future divergence (a body domain reading from `clean/body.md` instead of `raw/marker/document.md`) obvious and prevents accidental coupling.

**Risk:** Mechanical. Tests will catch any missed call site.

**Ship signal:** Test suite green, no behavior change.

## Step 2 — Scope `reconcile.py` to the equations domain

**Change:** Rename `canonical_raw_markdown` and the policy keys to make it explicit this is the equations domain reconciler. Adjust the system prompt to say "equations" rather than "academic PDF extraction evidence" generically. Tighten the packet to drop fields that pretend it's a general framework.

**Why:** The code only handles equations today and per the spec it should *only* handle contested domains. Rename matches reality and stops future hands from grafting other domains onto this module.

**Risk:** Tests reference the current key names — straightforward update.

**Ship signal:** Existing reconcile tests pass with renamed keys; the module's docstring and CLI help text say "equations" not "document."

## Step 3 — Auto-run GROBID during `extract_book` (hard requirement)

**Change:** `extract_book` calls the references extractor after Marker, unconditionally. Writes `raw/grobid/references.tei.xml` + `clean/references.csl.json`. If `GROBID_BASE_URL` is not configured, or GROBID is unreachable, or it returns garbage, extraction **hard-fails** with a clear error. No silent skipping.

**Why:** Per the spec, every extractor is required. A broken extraction pipeline should be a visible failure, not quietly missing data. The "let me just skip GROBID this once" branch is exactly the kind of fallback that hides problems for months.

**Risk:** Existing extractions of PDFs will start failing if GROBID isn't reachable. That's the point — they were already silently incomplete. Need to confirm GROBID is reachable from the extraction host (ms-01 / Spark) before this lands.

**Ship signal:** Re-extracting a paper produces both `raw/marker/` and `raw/grobid/`. With GROBID down, extraction fails loudly with a pointer to the GROBID service.

## Step 4 — Introduce the first domain builder

**Change:** Create `librarian/domain_references.py` with one function: `build_references(book_dir) -> ReferencesResult`. It calls GROBID (existing code), writes the CSL-JSON, runs the count sanity check against the marker visible bibliography (existing references_qa logic), and writes a small `review/references_qa.json`.

The existing `references.py` becomes purely the GROBID wire protocol (call + TEI→CSL mapping). `references_qa.py` becomes the visible-list reader. The *orchestration* (call extractor, write clean, sanity-check) moves to the builder.

**Why:** Makes "builder per domain" a real pattern with one example, so future builders have a template. Keeps responsibilities clean: extractor = how to talk to a tool, qa = how to inspect raw output, builder = how to produce the domain's clean artifact.

**Risk:** Small. Mostly moving code. Tests follow the function.

**Ship signal:** `extract_book` calls `build_references` (replacing the inlined GROBID code from step 3). One function defines the entire references domain pipeline.

## Step 5 — Per-domain MCP tool: `search_references`

**Change:** Add an MCP tool `search_references(query, library=None, author=None, year=None)` that reads `clean/references.csl.json` files across the library and returns structured CSL records (title, authors, year, DOI). Not an embedding search — this is structured query.

**Why:** Proves the per-domain RAG pattern with the simplest case. Validates that the architecture pays off — an agent asking "find all papers by Mindlin" gets clean records back, not text fragments.

**Risk:** New surface. Doesn't touch existing `search`. Easy to remove if the shape turns out wrong.

**Ship signal:** Calling `search_references(author="Mindlin")` returns matching CSL records from the indexed library.

## Step 6 — Introduce `clean/body.md`, point indexing at it

**Change:** Add a trivial body builder: `build_body(book_dir)` that copies `raw/marker/document.md` to `clean/body.md`. Update `index.py` to read from `clean/body.md` instead of the marker path.

**Why:** First behavioral RAG change. Decouples the indexer from the Marker file path. From here, `clean/body.md` can diverge from raw (apply equation corrections inline, strip footers, etc.) without touching indexing.

**Risk:** This step touches RAG. Mitigate by making it a literal byte-for-byte copy initially so retrieval results don't change. A re-index is needed but the chunks should be identical.

**Ship signal:** Smoke-test retrieval quality (`tests_quality/test_retrieval_quality_smoke.py`) returns identical or near-identical results before and after.

## Step 7 — Equations builder and contested reconciliation

**Change:** Create `librarian/domain_equations.py` with `build_equations(book_dir)` that:
1. Runs the marker↔pdftext comparator (existing `extraction_qa.py` logic).
2. Writes `clean/equations.json` with the agreed equations (status=ok) directly applied.
3. Returns the contested findings.

Reconciliation becomes an *optional* step: if `OPENAI_BASE_URL` is configured and contested findings exist, the equations builder calls `reconcile.py`'s equation patcher and writes `corrections.json`. Otherwise, contested equations are flagged and `clean/equations.json` includes them with `status=contested`.

**Why:** Captures the "reconciliation is the exception" rule in code. Most equations agree across extractors and just flow through; only the contested ones invoke the LLM, with per-finding evidence as already implemented.

**Risk:** Equations builder is new code on top of existing pieces. Tests can move from `test_extraction_qa.py` + `test_reconcile.py` into `test_domain_equations.py`.

**Ship signal:** Re-running extraction on Book 002 produces a `clean/equations.json` with Eq. 10 marked contested (without LLM) or with a `corrections.json` patch (with LLM). The Marker `\phi_i`/`\phi_j` bug becomes a tracked finding, not a silent loss.

## Step 8 — Per-domain MCP tool: `search_equations`

**Change:** Add `search_equations(query, book_id=None)` that returns equation records with both LaTeX and glyph forms, equation number, and the surrounding context window.

**Why:** Second proof of per-domain RAG. Confirms the pattern works for a domain that's not just structured metadata.

**Risk:** Same as step 5 — additive, doesn't touch existing search.

**Ship signal:** "What is the form of Equation 10 in Gardner 2001?" returns the equation record, not a prose fragment.

---

## What's deliberately not in this plan

- **Code, figures, citations as full domains.** The pattern is established by steps 4 + 7; those domains follow the same shape when they're worth building.
- **Docling, table extractors, code extractors.** New extractors are easy to add once the layout is in place. Not a prerequisite.
- **A general Extractor or Domain interface.** Builders are plain functions. Add abstractions only when the third domain forces it.
- **Re-indexing every existing book.** Step 6 requires a re-index, but the byte-identical copy keeps chunks stable so it can be staged book-by-book.
- **Removing Marker.** Marker stays the primary body extractor for the foreseeable future. The point is to stop *privileging* it in code paths that aren't about body text.

## Order of execution

Steps 1, 2 are pure renames and can happen in any order, including together. Step 3 unlocks step 4. Step 5 only needs steps 3+4. Step 6 stands alone but should land before step 7 since the equations builder writes alongside body. Steps 7 and 8 are the equations pair.

A reasonable shipping order: 1 → 2 → 3 → 4 → 5 → (pause, verify references domain works end-to-end via MCP) → 6 → 7 → 8.

If energy runs out partway, every step up to that point leaves the system in a working state. There is no "must finish all of it or roll back."
