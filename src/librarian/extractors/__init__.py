"""Extractors: each one writes its own raw/<name>/ artifacts.

The shape is one function per extractor module:

    def extract(source: Path, book_dir: Path) -> None:
        '''Extract source into book_dir/raw/<name>/. Raise on any failure.'''

No common base class. No registry. Add a new extractor by adding a new
module with an `extract` function and one line at the orchestration site.
"""

from librarian.extractors import grobid, marker, pdftext

__all__ = ["grobid", "marker", "pdftext"]
