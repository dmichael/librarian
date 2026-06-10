"""Expose extracted document images as stable, URL-addressable assets."""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

from librarian.catalog import public_base_url
from librarian.config import expand_path
from librarian.files import marker_content_json, marker_dir
from librarian.htmltext import html_to_text


IMAGE_ID_PREFIX = "marker:"
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_MARKER_FILENAME_RE = re.compile(
    r"^_page_(?P<page>\d+)_(?P<kind>[A-Za-z]+)_(?P<index>\d+)$"
)
_MARKER_REF_RE = re.compile(
    r"/?page/(?P<page>\d+)/(?P<kind>[A-Za-z]+)/(?P<index>\d+)"
)
_IMG_SRC_RE = re.compile(r"<img\b[^>]*\bsrc=['\"]([^'\"]+)['\"]", re.IGNORECASE)
_LABEL_RE = re.compile(r"\b(?P<label>(?:Figure|Fig\.|Table)\s+[A-Za-z0-9][A-Za-z0-9.\-]*)")


@dataclass(frozen=True)
class _ImageBlock:
    stem: str
    block_type: str | None = None
    page: int | None = None
    bbox: list | None = None
    caption: str | None = None
    label: str | None = None


def list_book_images(config: dict, book_id: int) -> dict:
    """Return metadata and URLs for images extracted from one book."""
    book_dir = _book_dir(config, book_id)
    image_dir = marker_dir(book_dir) / "images"
    if not image_dir.exists():
        return {
            "success": True,
            "book_id": book_id,
            "count": 0,
            "images": [],
        }

    block_index = _load_marker_image_blocks(book_dir)
    images = [
        _image_record(config, book_id, path, block_index.get(path.stem))
        for path in sorted(image_dir.iterdir(), key=_image_sort_key)
        if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
    ]

    return {
        "success": True,
        "book_id": book_id,
        "count": len(images),
        "images": images,
    }


def get_book_image(config: dict, book_id: int, image_id: str) -> dict:
    """Return metadata and URL for one extracted image."""
    path, error = resolve_image_path(config, book_id, image_id)
    if error:
        return {"success": False, "error": error}

    book_dir = _book_dir(config, book_id)
    block_index = _load_marker_image_blocks(book_dir)
    return {
        "success": True,
        "book_id": book_id,
        "image": _image_record(config, book_id, path, block_index.get(path.stem)),
    }


def resolve_image_path(
    config: dict, book_id: int, image_id: str
) -> tuple[Path | None, str | None]:
    """Resolve a public image_id to a file under raw/marker/images.

    Returns (path, error). The path is guaranteed to stay inside the canonical
    image directory.
    """
    filename = _filename_from_image_id(image_id)
    if not filename:
        return None, "Invalid image_id"

    book_dir = _book_dir(config, book_id)
    image_dir = (marker_dir(book_dir) / "images").resolve()
    path = (image_dir / filename).resolve()

    try:
        path.relative_to(image_dir)
    except ValueError:
        return None, "Invalid image_id"

    if not path.exists() or not path.is_file():
        return None, f"Image not found: {image_id}"
    if path.suffix.lower() not in _IMAGE_EXTENSIONS:
        return None, f"Unsupported image type: {path.suffix}"

    return path, None


def _book_dir(config: dict, book_id: int) -> Path:
    return expand_path(config["output_path"]) / str(book_id)


def _image_record(
    config: dict, book_id: int, path: Path, block: _ImageBlock | None
) -> dict:
    parsed = _parse_marker_stem(path.stem)
    image_id = f"{IMAGE_ID_PREFIX}{path.name}"
    page = parsed.get("page") if parsed else None
    kind = _kind_from_marker(parsed.get("kind") if parsed else None)
    if block and block.block_type == "FigureGroup" and kind == "unknown":
        kind = "figure"

    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    record = {
        "image_id": image_id,
        "asset_type": "image",
        "kind": kind,
        "source": "marker",
        "filename": path.name,
        "url": (
            f"{public_base_url(config)}/books/{book_id}/images/"
            f"{quote(image_id, safe='')}"
        ),
        "content_type": content_type,
        "size_bytes": path.stat().st_size,
    }

    if page is not None:
        record["page"] = page
    if parsed and parsed.get("index") is not None:
        record["index"] = parsed["index"]
    if block:
        if block.page is not None:
            record["marker_page"] = block.page
        if block.block_type:
            record["block_type"] = block.block_type
        if block.bbox:
            record["bbox"] = block.bbox
        if block.caption:
            record["caption"] = block.caption
        if block.label:
            record["label"] = block.label

    return record


def _load_marker_image_blocks(book_dir: Path) -> dict[str, _ImageBlock]:
    chunks_path = marker_content_json(book_dir)
    if not chunks_path:
        return {}

    try:
        data = json.loads(chunks_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}

    blocks = data if isinstance(data, list) else data.get("blocks", data.get("chunks", []))
    index: dict[str, _ImageBlock] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        stems = _stems_for_block(block)
        if not stems:
            continue
        html = block.get("html") or ""
        caption = html_to_text(html, "flat") or None
        label = _extract_label(caption)
        image_block = _ImageBlock(
            stem="",
            block_type=block.get("block_type"),
            page=block.get("page") if isinstance(block.get("page"), int) else None,
            bbox=block.get("bbox") if isinstance(block.get("bbox"), list) else None,
            caption=caption,
            label=label,
        )
        for stem in stems:
            index.setdefault(
                stem,
                _ImageBlock(
                    stem=stem,
                    block_type=image_block.block_type,
                    page=image_block.page,
                    bbox=image_block.bbox,
                    caption=image_block.caption,
                    label=image_block.label,
                ),
            )
    return index


def _stems_for_block(block: dict) -> set[str]:
    stems: set[str] = set()
    images = block.get("images")
    if isinstance(images, dict):
        for ref in images:
            if stem := _stem_from_marker_ref(ref):
                stems.add(stem)
    html = block.get("html") or ""
    for src in _IMG_SRC_RE.findall(html):
        if stem := _stem_from_marker_ref(src):
            stems.add(stem)
    return stems


def _stem_from_marker_ref(ref: str) -> str | None:
    name = Path(ref).name
    if Path(name).suffix.lower() in _IMAGE_EXTENSIONS:
        return Path(name).stem

    if match := _MARKER_REF_RE.search(ref):
        return (
            f"_page_{match.group('page')}_"
            f"{match.group('kind')}_{match.group('index')}"
        )
    return None


def _parse_marker_stem(stem: str) -> dict | None:
    if not (match := _MARKER_FILENAME_RE.match(stem)):
        return None
    return {
        "page": int(match.group("page")),
        "kind": match.group("kind"),
        "index": int(match.group("index")),
    }


def _kind_from_marker(kind: str | None) -> str:
    normalized = (kind or "").lower()
    if normalized in {"figure", "picture", "table"}:
        return normalized
    return "unknown"


def _extract_label(caption: str | None) -> str | None:
    if not caption:
        return None
    if match := _LABEL_RE.search(caption):
        return match.group("label")
    return None


def _filename_from_image_id(image_id: str) -> str | None:
    image_id = unquote(image_id)
    if not image_id.startswith(IMAGE_ID_PREFIX):
        return None
    filename = image_id[len(IMAGE_ID_PREFIX):]
    if not filename or filename != Path(filename).name:
        return None
    return filename


def _image_sort_key(path: Path) -> tuple[int, str, int, str]:
    parsed = _parse_marker_stem(path.stem)
    if parsed:
        return parsed["page"], parsed["kind"], parsed["index"], path.name
    return 10**9, "", 10**9, path.name
