from pathlib import Path

from librarian.extraction_qa import (
    compare_marker_to_pdftext,
    extract_numbered_equations_from_pdftext,
    write_extraction_qa,
)


def test_extract_numbered_equations_from_two_column_pdftext():
    text = """
             P_b = P_o + A cos[phi(t) + phi_i] ,                (9)    right column prose
              K = K_o + B cos[phi(t) + phi_j] .                (10)    more prose
"""

    equations = extract_numbered_equations_from_pdftext(text)

    assert [(eq.number, eq.text) for eq in equations] == [
        ("9", "P_b = P_o + A cos[phi(t) + phi_i] ,"),
        ("10", "K = K_o + B cos[phi(t) + phi_j] ."),
    ]


def test_compare_marker_to_pdftext_flags_disagreement(tmp_path: Path):
    marker_json = tmp_path / "2.json"
    marker_json.write_text(
        """
{
  "blocks": [
    {
      "block_type": "Equation",
      "html": "<p block-type=\\"Equation\\"><math display=\\"block\\">P_b = P_o + A\\\\cos[\\\\phi(t) + \\\\phi_i], \\\\tag{9}</math></p>"
    },
    {
      "block_type": "Equation",
      "html": "<p block-type=\\"Equation\\"><math display=\\"block\\">K = K_o + B\\\\cos[\\\\phi(t) + \\\\phi_i]. \\\\tag{10}</math></p>"
    }
  ]
}
"""
    )
    pdftext = tmp_path / "layout.txt"
    pdftext.write_text(
        """
             P_b = P_o + A cos[phi(t) + phi_i] ,                (9)
              K = K_o + B cos[phi(t) + phi_j] .                (10)
"""
    )

    comparisons = compare_marker_to_pdftext(marker_json, pdftext)
    by_number = {item.number: item for item in comparisons}

    # Eq 9: extractors agree modulo whitespace.
    # Eq 10: marker has phi_i, pdftext has phi_j — generic string mismatch flags review.
    assert by_number["10"].status == "review"
    assert "disagree" in by_number["10"].notes[0].lower()


def test_extraction_qa_writes_report(tmp_path: Path, monkeypatch):
    marker_dir = tmp_path / "raw" / "marker"
    marker_dir.mkdir(parents=True)
    (marker_dir / "document.json").write_text(
        """
{
  "blocks": [
    {
      "block_type": "Equation",
      "html": "<p block-type=\\"Equation\\"><math display=\\"block\\">K = K_o + B\\\\cos[\\\\phi(t) + \\\\phi_i]. \\\\tag{10}</math></p>"
    }
  ]
}
"""
    )

    def fake_extract(source, book_dir):
        pdftext_dir = book_dir / "raw" / "pdftext"
        pdftext_dir.mkdir(parents=True)
        (pdftext_dir / "layout.txt").write_text(
            "K = K_o + B cos[phi(t) + phi_j] .               (10)"
        )

    monkeypatch.setattr("librarian.extractors.pdftext.extract", fake_extract)

    write_extraction_qa(tmp_path / "source.pdf", tmp_path)

    report = (tmp_path / "review" / "extraction_qa.md").read_text()
    assert "Equation Comparison" in report
    assert "review" in report
