"""Tests for MCP server helpers."""

import inspect

from librarian.catalog import build_vector_metadata_updates
from librarian.mcp_server import get_book_image, list_book_images, search


def test_book_vector_metadata_updates_includes_display_and_filter_fields():
    updates = build_vector_metadata_updates(
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
    assert build_vector_metadata_updates(authors=["Guglielmo Iozzia"]) == {
        "authors": "Guglielmo Iozzia",
    }


def test_search_tool_exposes_block_type_filter():
    signature = inspect.signature(search)

    assert "block_type" in signature.parameters
    assert signature.parameters["block_type"].default is None


def test_image_tools_expose_expected_parameters():
    list_signature = inspect.signature(list_book_images)
    get_signature = inspect.signature(get_book_image)

    assert list(list_signature.parameters) == ["book_id"]
    assert list(get_signature.parameters) == ["book_id", "image_id"]


def test_search_result_rows_include_images_field():
    from librarian.metadata_types import build_search_result_row

    row = build_search_result_row(
        text="Figure 2.3 explains LoRA.",
        score=0.98765,
        metadata={"book_id": 169, "title": "Domain-Specific Small Language Models"},
    )

    assert row["images"] == []

    with_image = build_search_result_row(
        text="Figure 2.3 explains LoRA.",
        score=0.98765,
        metadata={"book_id": 169, "title": "Domain-Specific Small Language Models"},
        images=[{"image_id": "marker:_page_50_Figure_12.jpeg"}],
    )

    assert with_image["images"] == [{"image_id": "marker:_page_50_Figure_12.jpeg"}]
