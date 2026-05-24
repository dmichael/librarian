from pathlib import Path

from librarian.extraction_qa import (
    compare_marker_to_pdftext,
    extract_numbered_equations_from_pdftext,
)


def test_extract_numbered_equations_from_two_column_pdftext():
    text = """
             Pb 苷 Po 1 A cos关f共t兲 1 fi 兴 ,                (9)    right column prose
              K 苷 Ko 1 B cos关f共t兲 1 fj 兴 .               (10)    more prose
"""

    equations = extract_numbered_equations_from_pdftext(text)

    assert [(eq.number, eq.text) for eq in equations] == [
        ("9", "Pb 苷 Po 1 A cos关f共t兲 1 fi 兴 ,"),
        ("10", "K 苷 Ko 1 B cos关f共t兲 1 fj 兴 ."),
    ]


def test_compare_marker_to_pdftext_flags_phase_symbol_disagreement(tmp_path: Path):
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
             Pb 苷 Po 1 A cos关f共t兲 1 fi 兴 ,                (9)
              K 苷 Ko 1 B cos关f共t兲 1 fj 兴 .               (10)
"""
    )

    comparisons = compare_marker_to_pdftext(marker_json, pdftext)
    by_number = {item.number: item for item in comparisons}

    assert by_number["9"].status == "ok"
    assert by_number["10"].status == "review"
    assert "phi_j" in by_number["10"].notes[0]
