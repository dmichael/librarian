"""Equation fidelity: load_extracted_blocks must preserve <math> markup so the
equation extractor recovers clean LaTeX rather than markdownified text."""

import json
from pathlib import Path

from librarian.equations import extract_equations_from_blocks
from librarian.files import marker_dir
from librarian.index import load_extracted_blocks

# Marker emits clean LaTeX inside <math> and leaves the block's text empty.
EQ_HTML = (
    '<p block-type="Equation"><math display="block">'
    r"a = a_0 + \tau \frac{dx}{dt}, \tag{1}"
    "</math></p>"
)


def _write_marker_doc(book_dir: Path, blocks: list[dict]) -> None:
    md = marker_dir(book_dir)
    md.mkdir(parents=True, exist_ok=True)
    (md / "document.json").write_text(json.dumps({"blocks": blocks}))


def test_load_blocks_preserves_equation_html(tmp_path: Path):
    _write_marker_doc(tmp_path, [
        {"block_type": "Text", "html": "<p>We derive the relation.</p>", "text": "", "page": 1},
        {"block_type": "Equation", "html": EQ_HTML, "text": "", "page": 1},
    ])
    blocks = load_extracted_blocks(tmp_path)
    assert blocks is not None
    eq_block = next(b for b in blocks if b["block_type"] == "Equation")
    assert "<math" in eq_block["html"]


def test_equation_latex_recovered_clean(tmp_path: Path):
    _write_marker_doc(tmp_path, [
        {"block_type": "Text", "html": "<p>We derive the relation.</p>", "text": "", "page": 1},
        {"block_type": "Equation", "html": EQ_HTML, "text": "", "page": 1},
    ])
    blocks = load_extracted_blocks(tmp_path)
    eqs = extract_equations_from_blocks(blocks)

    assert len(eqs) == 1
    # Clean LaTeX recovered from <math>, not the markdownified/mangled text.
    assert r"\frac{dx}{dt}" in eqs[0].latex
    assert r"\tau" in eqs[0].latex
    assert eqs[0].equation_number == "1"
