"""Tests for MCP server helpers."""

from librarian.mcp_server import _book_vector_metadata_updates


def test_book_vector_metadata_updates_includes_display_and_filter_fields():
    updates = _book_vector_metadata_updates(
        title="Domain-Specific Small Language Models",
        authors=["Guglielmo Iozzia"],
        subjects=["cs/small-language-models", "cs/fine-tuning"],
        library="fine-tuning",
    )

    assert updates == {
        "title": "Domain-Specific Small Language Models",
        "authors": "Guglielmo Iozzia",
        "subjects": '["cs/small-language-models", "cs/fine-tuning"]',
        "library": "fine-tuning",
    }


def test_book_vector_metadata_updates_omits_unchanged_fields():
    assert _book_vector_metadata_updates(authors=["Guglielmo Iozzia"]) == {
        "authors": "Guglielmo Iozzia",
    }
