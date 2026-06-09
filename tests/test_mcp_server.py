"""Tests for MCP server helpers."""

import inspect

from librarian.mcp_server import _book_vector_metadata_updates, search


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


def test_search_tool_exposes_block_type_filter():
    signature = inspect.signature(search)

    assert "block_type" in signature.parameters
    assert signature.parameters["block_type"].default is None
