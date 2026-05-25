# Feature: Multi-Extractor, Per-Domain Extraction

## Summary

Treat PDF extraction as a multi-extractor, per-domain pipeline whose output is consumed by domain-aware RAG — not as "Marker plus checks."

## Problem

The current pipeline implicitly treats Marker as canonical raw and uses other extractors (pdftext, GROBID) only as cross-checks against it. Helpers in `files.py` hardcode `raw/marker/` as "the" content; `reconcile.py` calls Marker's markdown the `canonical_raw_markdown`. As a result:

- Multi-modal extraction is not actually multi-modal — Marker is privileged.
- All extracted content is flattened into one text stream and embedded. RAG can only do "find text near your query," which hurts citations, equations, references, and code — the things RAG users most need to be accurate.
- Per-pair extractor comparators are hand-tuned to single books (the `\phi_X` regex and Phys-Rev-Letters mojibake set were derived from Book 002 alone). This doesn't generalize.

## Requirements

- [ ] **Extractors are equal and content-agnostic.** Each extractor writes its own native artifacts under `raw/<extractor>/` per book. None is privileged in code. Every extractor runs on every document. Empty output is valid output.
- [ ] **No fallbacks, no silent failures.** Every extractor is required. If an extractor's service is unreachable or misconfigured, extraction hard-fails with a clear error. There is no "skip GROBID if it's down" branch — a broken pipeline is a visible problem, not a quietly degraded one. Empty *content* output (zero references found) is valid; an *unreachable extractor* is not.
- [ ] **No document-type taxonomy.** There is no "article" vs "book" vs "manual" branching anywhere in the pipeline. Each extractor either produces content or doesn't.
- [ ] **Domains are first-class.** Each domain is a builder function (not a folder) that reads from one or more `raw/<extractor>/` outputs and writes a flat artifact under `clean/`:
  - `clean/body.md`
  - `clean/references.csl.json`
  - `clean/equations.json`
  - `clean/citations.json`
  - `clean/code.json`
  - `clean/figures.json`
- [ ] **Pick-a-winner per domain.** Each domain has one primary extractor that wins by default. Other extractors are used as sanity checks (count match, presence check, OCR cross-read), not as reconciliation inputs:

  | Domain | Primary | Cross-check |
  |---|---|---|
  | body | marker | (docling later) |
  | references | grobid | marker visible list (count sanity) |
  | citations | grobid | marker body (in-text presence) |
  | equations | marker (LaTeX) | pdftext (raw glyphs) — contested, see below |
  | code | dedicated extractor (TBD) | — |
  | figures | marker | pdftext (caption OCR) |

- [ ] **Reconciliation is the exception, not the rule.** It runs only in contested domains where two extractors have legitimate but different claims and neither is authoritative. Today that means equations (LaTeX form vs glyph ground truth). The existing `reconcile.py` machinery applies to that one domain.
- [ ] **RAG is per-domain.** Each domain produces its own index/retrieval surface. Body chunks stay where they are; references become a structured retrievable record set; equations become an equation index; citations become a graph. The MCP server gains per-domain search tools.

## Success Criteria

- [ ] `files.py` no longer hardcodes `raw/marker/` as a generic content location. Marker-specific helpers say so in their name.
- [ ] `reconcile.py` is explicitly scoped to the equations domain — its prompt, packet, and schema no longer pretend to be a general framework.
- [ ] References builds end-to-end inside `extract_book`: GROBID produces `raw/grobid/references.tei.xml` and the references builder writes `clean/references.csl.json`. The marker visible list is consulted only for the count sanity check.
- [ ] Per-domain RAG demonstrated for one domain (references is the obvious template): an MCP tool that returns structured records, not text fragments.
- [ ] Each extractor + each domain runs on every book regardless of perceived "type." A novel produces `clean/references.csl.json = []` and that is not an error.

## Out of Scope

- Rewriting the indexing layer. The current chunk-and-embed pipeline keeps working for the body domain.
- Adding new extractors (docling, code extractors, table extractors). Those are follow-ups; the design must accommodate them but doesn't require them up front.
- A general "Extractor" plugin interface or registry. Builders are plain functions. If a plugin system is needed later, it can be added.
- Document classification of any kind.

## Open Questions

- Where does the references builder live? A new `domains/` package, or flat modules at `librarian/`? (Lean: flat modules, e.g. `librarian/domain_references.py`, until there are enough builders to justify a package.)
- Should `clean/` be produced lazily (on first query) or eagerly (at extraction time)? Lean: eagerly. Cheap operations only — anything LLM-driven (reconciliation) stays opt-in.
- How are per-domain MCP tools named and scoped? `search_references`, `search_equations`, `search_body`? Or one parameterized tool with a `domain` argument?
- When a future extractor (docling, code) joins, does it replace the primary for its domain or just add cross-checks? Decide per case at the time, not in advance.
