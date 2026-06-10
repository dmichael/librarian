import json
from pathlib import Path

from librarian import images


def _config(tmp_path: Path) -> dict:
    return {
        "output_path": str(tmp_path),
        "public_url": "http://librarian.test",
    }


def test_list_book_images_exposes_marker_images_with_block_metadata(tmp_path: Path):
    book_dir = tmp_path / "42"
    marker_dir = book_dir / "raw" / "marker"
    image_dir = marker_dir / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "_page_27_Figure_2.jpeg").write_bytes(b"jpeg")

    (marker_dir / "document.json").write_text(json.dumps({
        "blocks": [
            {
                "block_type": "FigureGroup",
                "html": (
                    "<p><img src='/page/27/Figure/2'></p>"
                    "<p>Figure 1.1 Some examples of generated content</p>"
                ),
                "page": 27,
                "bbox": [1, 2, 3, 4],
                "images": {"/page/27/Figure/2": "..."},
            }
        ]
    }))

    result = images.list_book_images(_config(tmp_path), 42)

    assert result["success"] is True
    assert result["count"] == 1
    image = result["images"][0]
    assert image["image_id"] == "marker:_page_27_Figure_2.jpeg"
    assert image["asset_type"] == "image"
    assert image["kind"] == "figure"
    assert image["page"] == 27
    assert image["marker_page"] == 27
    assert image["block_type"] == "FigureGroup"
    assert image["bbox"] == [1, 2, 3, 4]
    assert image["label"] == "Figure 1.1"
    assert image["caption"] == "Figure 1.1 Some examples of generated content"
    assert image["content_type"] == "image/jpeg"
    assert image["size_bytes"] == 4
    assert (
        image["url"]
        == "http://librarian.test/books/42/images/marker%3A_page_27_Figure_2.jpeg"
    )


def test_list_book_images_classifies_picture_from_filename(tmp_path: Path):
    image_dir = tmp_path / "7" / "raw" / "marker" / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "_page_3_Picture_4.png").write_bytes(b"png")

    result = images.list_book_images(_config(tmp_path), 7)

    assert result["count"] == 1
    assert result["images"][0]["kind"] == "picture"
    assert result["images"][0]["content_type"] == "image/png"


def test_get_book_image_resolves_only_known_marker_image_ids(tmp_path: Path):
    image_dir = tmp_path / "7" / "raw" / "marker" / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "_page_3_Picture_4.png").write_bytes(b"png")

    result = images.get_book_image(_config(tmp_path), 7, "marker:_page_3_Picture_4.png")

    assert result["success"] is True
    assert result["image"]["filename"] == "_page_3_Picture_4.png"

    encoded = images.get_book_image(
        _config(tmp_path), 7, "marker%3A_page_3_Picture_4.png"
    )
    assert encoded["success"] is True
    assert encoded["image"]["filename"] == "_page_3_Picture_4.png"

    bad = images.get_book_image(_config(tmp_path), 7, "marker:../secret.png")
    assert bad == {"success": False, "error": "Invalid image_id"}


def test_list_book_images_returns_empty_for_unextracted_book(tmp_path: Path):
    result = images.list_book_images(_config(tmp_path), 99)

    assert result == {
        "success": True,
        "book_id": 99,
        "count": 0,
        "images": [],
    }


def test_images_for_metadata_links_search_result_by_book_and_marker_page(tmp_path: Path):
    book_dir = tmp_path / "42"
    marker_dir = book_dir / "raw" / "marker"
    image_dir = marker_dir / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "_page_27_Figure_2.jpeg").write_bytes(b"jpeg")
    (image_dir / "_page_28_Figure_5.jpeg").write_bytes(b"other")

    (marker_dir / "document.json").write_text(json.dumps({
        "blocks": [
            {
                "block_type": "FigureGroup",
                "html": (
                    "<p><img src='/page/27/Figure/2'></p>"
                    "<p>Figure 1.1 Some examples of generated content</p>"
                ),
                "page": 27,
                "bbox": [1, 2, 3, 4],
                "images": {"/page/27/Figure/2": "..."},
            },
            {
                "block_type": "FigureGroup",
                "html": (
                    "<p><img src='/page/28/Figure/5'></p>"
                    "<p>Figure 1.2 Another figure</p>"
                ),
                "page": 28,
                "images": {"/page/28/Figure/5": "..."},
            },
        ]
    }))

    linked = images.images_for_metadata(
        _config(tmp_path),
        {"book_id": 42, "page": 27, "block_type": "Text"},
    )

    assert len(linked) == 1
    assert linked[0]["image_id"] == "marker:_page_27_Figure_2.jpeg"
    assert linked[0]["caption"] == "Figure 1.1 Some examples of generated content"
    assert linked[0]["url"].endswith("/books/42/images/marker%3A_page_27_Figure_2.jpeg")
    assert "size_bytes" not in linked[0]
