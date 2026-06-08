"""Extractors: each one writes its own raw/<name>/ artifacts.

Each extractor module owns its native output AND normalized views:
  - marker: document.json, document.md, document.html, equations.json, images/
  - grobid: fulltext.tei.xml, references.csl.json, citations.json,
            sections.json, figures.json

No common base class. No registry. Add a new extractor by adding a new
module and one line at the orchestration site (extract.py).
"""

from librarian.extractors import grobid, marker

__all__ = ["grobid", "marker"]
