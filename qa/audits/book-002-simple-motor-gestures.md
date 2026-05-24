# Extraction Audit: Book 2

Book: Simple Motor Gestures for Birdsongs  
Authors: Tim Gardner, G. Cecchi, M. Magnasco, R. Laje, Gabriel B. Mindlin  
Source: Gardner et al. 2001, Physical Review Letters 87, 208101  
Audit target: current Marker extraction from `/Volumes/librarian/converted/2/raw/marker/document.md`

Use `pass`, `partial`, or `fail`. The goal is not perfect transcription; it is whether the extraction is good enough for grounded retrieval, synthetic QA generation, and later fine-tuning examples.

## Extraction Snapshot

- Extracted files: `raw/marker/document.md`, `raw/marker/document.json`, `raw/marker/metadata.json`, `raw/marker/document.html`, `raw/marker/html_metadata.json`, `raw/marker/images/*`
- Marker blocks: 62
- Logical pages in metadata: 4
- Page extraction methods:
  - page 0: `pdftext`
  - pages 1-3: `surya`
- Detected figures: 4 `FigureGroup` blocks
- Detected equations: 10 equation blocks
- Detected chapters: 0

## Landmark Checks

| ID | Landmark | What To Check | Status | Notes |
|---|---|---|---|---|
| L1 | Title/authors/abstract | Title, author split, affiliations, DOI/PACS, and abstract-like opening paragraph are readable and not scrambled. |  |  |
| L2 | Page 1 two-column flow | Opening body text reads in correct order across the two columns; no major column interleaving. | pass | Human review through page 1 found the two-column reading order fluid. Equations continue in correct order from page 1 column 2 to page 2 column 1. |
| L3 | Eq. 1-4 labial model | Equations for `a`, `b`, `P_f`, and the oscillator equation preserve symbols, subscripts, derivatives, and numbering. | pass | Semantic math content preserved. Numbering style is inconsistent (`\tag{1}` vs inline `(2)` etc.), but acceptable for RAG. |
| L4 | Figure 1 | Figure image placeholder is present and caption stays attached; caption content matches the PDF. | partial | Caption label `FIG. 1` is correct and attached. Markdown image path `![](/page/1/Figure/3)` is a detector-local image id, not the paper's figure number, which may confuse downstream consumers unless normalized. |
| L5 | Figure 2 | Parameter-space figure and caption are attached; `P_b`, `K`, units, and parameter values survive. | partial | Numeric values and variables survived. Minor cleanup needed: HTML `<sup>` leaks into Markdown for `N/cm<sup>3</sup>`, and `\mum` represents `μm`. |
| L6 | Filter equations 5-8 | Boundary-condition equations and surrounding explanation preserve `a(t)`, `b_b`, `b_f`, `c_b`, `tau_i`, `r_{1,2}`, `t_{1,2}`. | pass | Semantic content and order preserved. Equation numbering style remains inconsistent but usable. |
| L7 | Control equations 9-10 | `P_b = P_o + A cos[...]` and `K = K_o + B cos[...]` are readable, correctly numbered, and close to the relevant prose. | partial | Eq. 9 is correct. Eq. 10 has a semantic symbol error: PDF has `\phi_j`, but Markdown/HTML extraction has `\phi_i`. Needs manual correction before clean chunking. |
| L8 | Figure 3 | Natural/artificial syllable figure and long caption are attached; no serious caption truncation. |  |  |
| L9 | Continuation after Figure 3 | Text after Fig. 3 resumes correctly with the high-pressure parametrization, not before/inside the wrong column. |  |  |
| L10 | Figure 4 | Synthetic signal figure and caption are attached; subsequent prose resumes correctly. |  |  |
| L11 | Conclusion/acknowledgment | Final argument about elliptical paths and control of `P_b`/`K` is readable; acknowledgments are separated enough. |  |  |
| L12 | References | References are detected as a list and not mixed into the main body; reference 15 equation-heavy note is readable enough. |  |  |
| L13 | Code/model definitions | Equations or prose that define executable model structure, parameters, filters, or simulation assumptions are preserved well enough to later translate into code/pseudocode. |  |  |

## Known Extraction Observations To Verify

- The Markdown begins with several blank lines before the title.
- Page footer text like `208101-2 208101-2` appears in the body.
- Figure image references are placeholders like `![](/page/1/Figure/15)`, not extracted image files visible in Markdown.
- Equations appear mostly as LaTeX-ish text, but some surrounding prose includes raw `\text{...}` and HTML tags like `<sup>`.
- The metadata has odd page identifiers (`315`, `213`, `207`, `307`) in block rows; this may be PDF label/page metadata rather than page index.
- Eq. 10 extraction error: `K = K_o + B\cos[\phi(t) + \phi_j]` in the PDF was extracted as `K = K_o + B\cos[\phi(t) + \phi_i]`. This changes the phase parameter label and should be corrected in a clean/normalized artifact.
- Figure placeholders use Marker detector ids, e.g. `/page/1/Figure/3`, not paper figure numbers. Paper figure numbers should be recovered from captions.
- HTML appears to be the best human QA artifact; Markdown remains the primary candidate for chunking after normalization.
- `pdftotext -layout` on the embedded PDF text layer correctly distinguishes Eq. 10's `fj`/`\phi_j`, giving a useful cross-check against Marker.

## Raw Extractor Battery

This document is now the first fixture for the fixed empirical extraction battery:

| Extractor | Purpose | Artifact | Status | Notes |
|---|---|---|---|---|
| Marker | Main readable extraction: Markdown, JSON blocks, HTML, figures, equations. | `raw/marker/document.md`, `raw/marker/document.json`, `raw/marker/document.html`, `raw/marker/images/*` | active | Strong layout/reading-order result; one known equation symbol error in Eq. 10. |
| pdftotext | Embedded-text comparison baseline. | `raw/pdftext/layout.txt` | active on branch | Caught Eq. 10 `phi_j`; not suitable as primary Markdown because two-column layout and figure text are noisy. |
| GROBID | Scholarly structure, references, citation graph. | TBD | planned | Should be added before/with reference audit, because references are domain-graph inputs. |
| Docling | Full-document parser benchmark. | TBD | deferred | Useful later, but less urgent than GROBID for graph extraction. |

## Reference/Graph Checks

References should be evaluated as graph inputs, not just as readable text.

| ID | Check | Expected Evidence | Status | Notes |
|---|---|---|---|---|
| G1 | Reference count | PDF has references [1]-[22], all present exactly once. |  |  |
| G2 | Citation keys | In-text citations such as `[1]`, `[2]`, `[10,13]`, `[21,22]` remain attached to the relevant claims. |  |  |
| G3 | Bibliographic fields | Author names, venue, volume, pages, and year are recoverable for each reference. |  |  |
| G4 | Domain edges | Foundational works on vocal learning, syrinx physiology, vocal-fold models, and prior Mindlin/Trevisan work are identifiable. |  |  |
| G5 | Reference 15 | Equation-heavy explanatory note remains readable and separate from the main bibliography. |  |  |

## Code/Model Checks

This paper has no source-code block, but it has model definitions that are code-relevant.

| ID | Check | Expected Evidence | Status | Notes |
|---|---|---|---|---|
| C1 | Dynamical system | Eq. 4 and the nonlinear dissipation explanation are preserved enough to implement the oscillator. |  |  |
| C2 | Boundary filter | Eqs. 5-8 preserve wave variables and delays well enough to implement the vocal-tract filter. |  |  |
| C3 | Control trajectory | Eqs. 9-10 preserve separate phase offsets `\phi_i` and `\phi_j`; Eq. 10 currently fails in Marker and needs correction. | partial | `pdftotext` caught the correct `fj`/`\phi_j`. |
| C4 | Parameter values | Fig. 2 and Fig. 3 captions preserve numeric parameters and units for simulation. |  |  |
| C5 | Algorithmic limitations | Text stating fitting was qualitative and algorithmic fitting was work in progress is preserved. |  |  |

## Retrieval Probe Checks

These are questions the RAG should retrieve from this paper. For each, check whether the retrieved passage would let a model answer faithfully.

| ID | Probe | Expected Evidence | Status | Notes |
|---|---|---|---|---|
| R1 | What two control parameters does the model assume the bird controls? | Bronchial pressure `P_b` and labial elasticity/tension `K`. |  |  |
| R2 | What does Figure 2 represent? | Sound production as a function of `P_b` and `K`, with oscillation boundary and isofundamental contours. |  |  |
| R3 | What is the form of the elliptical control trajectories? | Equations 9 and 10 for `P_b` and `K` using cosines of `\phi(t) + \phi_i` and `\phi(t) + \phi_j`, respectively. |  | Eq. 10 must be corrected before this retrieval probe can be trusted. |
| R4 | What role does pressure play versus tension? | `K` traces fundamental frequency; pressure affects amplitude and harmonics. |  |  |
| R5 | What is the paper's broad conclusion? | Many canary syllables can be modeled through simple, smooth cycles in pressure/tension parameter space. |  |  |

## Initial Scoring

Overall extraction quality:

- Page coverage:
- Reading order:
- Equation fidelity: partial/pass overall; equations 1-8 pass, Eq. 9 passes, Eq. 10 has one semantic symbol error (`\phi_j` extracted as `\phi_i`).
- Figure/caption fidelity: partial; captions are attached and semantically useful, but image placeholder names are not paper figure numbers.
- Noise/footer handling: partial; page footers such as `208101-2 208101-2` are present and should be removed for clean chunking.
- Retrieval usefulness: likely good after light normalization and the Eq. 10 correction.

Decision:

- Accept current extraction: yes, as raw provenance and baseline.
- Needs Marker tuning: investigate, but no obvious exposed knob is likely to fix the `\phi_j`/`\phi_i` visual-symbol confusion by itself.
- Needs alternative parser comparison: yes, eventually useful for equation-heavy papers, especially against Docling/GROBID or another math-capable path.
- Needs manual cleanup/gold annotations: yes; create a clean Markdown artifact before indexing/chunking this paper.
