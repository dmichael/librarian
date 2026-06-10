"""Read deterministic source spans from indexed document structure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from librarian.config import expand_path
from librarian.files import (
    load_extracted_blocks,
    marker_content_json,
    structure_json,
    structure_json_path,
)
from librarian.structure import DocumentStructure

DEFAULT_MAX_CHARS = 12_000
HARD_MAX_CHARS = 50_000

# Parsed-block cache for the long-running MCP server: read_span is paginated,
# so each cursor continuation would otherwise re-parse and re-markdownify the
# whole book. Keyed by (path, mtime) so a re-extract invalidates naturally.
_BLOCKS_CACHE_SIZE = 4
_blocks_cache: dict[tuple[str, float], list[dict]] = {}


def save_structure_artifact(
    config: dict,
    book_id: int,
    structure: DocumentStructure,
    source: str,
    audit: dict | None,
    block_count: int,
) -> Path:
    """Persist the structure used for indexing as a source-read artifact."""
    book_dir = expand_path(config["output_path"]) / str(book_id)
    path = structure_json_path(book_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _structure_artifact(book_id, structure, source, audit, block_count),
            indent=2,
            sort_keys=True,
        )
    )
    return path


def list_spans(config: dict, book_id: int) -> dict:
    """List readable scopes for a book from its persisted structure artifact."""
    artifact, error = _load_artifact(config, book_id)
    if error:
        return {"success": False, "book_id": book_id, "error": error}

    available = ["book"]
    if artifact.get("chapters"):
        available.append("chapter")
    if _all_sections(artifact):
        available.append("section")

    return {
        "success": True,
        "book_id": book_id,
        "title": artifact.get("title", ""),
        "structure_source": artifact.get("source", ""),
        "available_scopes": available,
        "chapters": artifact.get("chapters", []),
        "supports_images": True,
    }


def read_span(
    config: dict,
    book_id: int,
    scope: str,
    chapter: int | None = None,
    section: str | None = None,
    cursor: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_images: bool = False,
) -> dict:
    """Read ordered source blocks for a book/chapter/section span.

    Requires the structure artifact written during indexing.  This function
    never runs the LLM audit or silently rebuilds structure at request time.
    """
    artifact, error = _load_artifact(config, book_id)
    if error:
        return {"success": False, "book_id": book_id, "error": error}

    blocks = _load_blocks(config, book_id)
    if blocks is None:
        return {
            "success": False,
            "book_id": book_id,
            "error": "source blocks missing; re-extract and reindex required",
        }

    scope = scope.lower().strip()
    selected, span, error = _select_indices(artifact, scope, chapter, section)
    if error:
        return {"success": False, "book_id": book_id, "error": error}

    start_at, cursor_error = _parse_cursor(cursor)
    if cursor_error:
        return {"success": False, "book_id": book_id, "error": cursor_error}
    if start_at is not None:
        selected = [idx for idx in selected if idx >= start_at]

    budget = min(max(max_chars, 1), HARD_MAX_CHARS)
    out_blocks: list[dict] = []
    total_chars = 0
    next_cursor = None

    chapter_map = _int_key_map(artifact.get("block_to_chapter", {}))
    section_map = _str_key_map(artifact.get("block_to_section", {}))
    chapter_titles = {
        int(ch["number"]): ch.get("title", "")
        for ch in artifact.get("chapters", [])
        if ch.get("number") is not None
    }

    for idx in selected:
        if idx >= len(blocks):
            continue
        text = blocks[idx].get("text") or ""
        if not text:
            continue
        if out_blocks and total_chars + len(text) > budget:
            next_cursor = f"block:{idx}"
            break
        out_blocks.append({
            "block_idx": idx,
            "page": blocks[idx].get("page"),
            "block_type": blocks[idx].get("block_type"),
            "chapter_num": chapter_map.get(idx),
            "chapter_title": chapter_titles.get(chapter_map.get(idx), ""),
            "section_title": section_map.get(idx, ""),
            "text": text,
        })
        total_chars += len(text)

    pages = {
        block["page"] for block in out_blocks if isinstance(block.get("page"), int)
    }
    result = {
        "success": True,
        "book_id": book_id,
        "title": artifact.get("title", ""),
        "structure_source": artifact.get("source", ""),
        "scope": scope,
        "span": span,
        "cursor": cursor,
        "next_cursor": next_cursor,
        "max_chars": budget,
        "text": "\n\n".join(block["text"] for block in out_blocks),
        "blocks": out_blocks,
    }

    if include_images:
        result["images"] = _images_for_pages(config, book_id, pages)

    return result


def _structure_artifact(
    book_id: int,
    structure: DocumentStructure,
    source: str,
    audit: dict | None,
    block_count: int,
) -> dict:
    block_to_chapter = {str(k): v for k, v in structure.block_to_chapter.items()}
    block_to_section = {str(k): v for k, v in structure.block_to_section.items()}
    chapters = []
    for chapter_obj in structure.chapters:
        chapter = chapter_obj.model_dump()
        start, end = _range_for_chapter(structure, chapter_obj.number)
        chapter["start_block_idx"] = start
        chapter["end_block_idx"] = end
        for section in chapter["sections"]:
            sec_start, sec_end = _range_for_section(
                structure, section["title"], chapter_obj.number
            )
            section["start_block_idx"] = sec_start
            section["end_block_idx"] = sec_end
        chapters.append(chapter)
    return {
        "book_id": book_id,
        "title": structure.title,
        "source": source,
        "audit": audit or {"applied": False, "reason": ""},
        "block_count": block_count,
        "book_sections": [section.model_dump() for section in structure.book_sections],
        "chapters": chapters,
        "block_to_chapter": block_to_chapter,
        "block_to_section": block_to_section,
    }


def _range_for_chapter(
    structure: DocumentStructure, chapter_num: int
) -> tuple[int | None, int | None]:
    indices = [
        idx for idx, num in structure.block_to_chapter.items() if num == chapter_num
    ]
    return (min(indices), max(indices)) if indices else (None, None)


def _range_for_section(
    structure: DocumentStructure, title: str, chapter_num: int | None = None
) -> tuple[int | None, int | None]:
    indices = []
    for idx, section in structure.block_to_section.items():
        if section != title:
            continue
        if chapter_num is not None and structure.block_to_chapter.get(idx) != chapter_num:
            continue
        indices.append(idx)
    return (min(indices), max(indices)) if indices else (None, None)


def _load_artifact(config: dict, book_id: int) -> tuple[dict | None, str | None]:
    book_dir = expand_path(config["output_path"]) / str(book_id)
    path = structure_json(book_dir)
    if not path:
        return None, "structure artifact missing; reindex required"
    try:
        return json.loads(path.read_text()), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"structure artifact unreadable: {exc}"


def _load_blocks(config: dict, book_id: int) -> list[dict] | None:
    book_dir = expand_path(config["output_path"]) / str(book_id)
    path = marker_content_json(book_dir)
    if not path:
        return None

    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None
    cached = _blocks_cache.get(key)
    if cached is not None:
        return cached

    try:
        blocks = load_extracted_blocks(book_dir)
    except (OSError, json.JSONDecodeError):
        return None
    if blocks is None:
        return None

    while len(_blocks_cache) >= _BLOCKS_CACHE_SIZE:
        _blocks_cache.pop(next(iter(_blocks_cache)))
    _blocks_cache[key] = blocks
    return blocks


def _select_indices(
    artifact: dict,
    scope: str,
    chapter: int | None,
    section: str | None,
) -> tuple[list[int], dict, str | None]:
    block_count = int(artifact.get("block_count") or 0)
    chapter_map = _int_key_map(artifact.get("block_to_chapter", {}))
    section_map = _str_key_map(artifact.get("block_to_section", {}))

    if scope == "book":
        return list(range(block_count)), {"type": "book"}, None

    if scope == "chapter":
        if chapter is None:
            return [], {}, "chapter is required for chapter scope"
        selected = [idx for idx, num in chapter_map.items() if num == chapter]
        if not selected:
            return [], {}, f"chapter {chapter} not found"
        title = _chapter_title(artifact, chapter)
        return selected, {"type": "chapter", "number": chapter, "title": title}, None

    if scope == "section":
        if not section:
            return [], {}, "section is required for section scope"
        selected = [
            idx
            for idx, title in section_map.items()
            if title == section and (chapter is None or chapter_map.get(idx) == chapter)
        ]
        if not selected:
            return [], {}, f"section not found: {section}"
        span = {"type": "section", "title": section}
        if chapter is not None:
            span["chapter"] = chapter
            span["chapter_title"] = _chapter_title(artifact, chapter)
        return selected, span, None

    return [], {}, f"unsupported scope: {scope}"


def _chapter_title(artifact: dict, chapter_num: int) -> str:
    for chapter in artifact.get("chapters", []):
        if chapter.get("number") == chapter_num:
            return chapter.get("title", "")
    return ""


def _all_sections(artifact: dict) -> list[str]:
    seen = []
    for title in _str_key_map(artifact.get("block_to_section", {})).values():
        if title and title not in seen:
            seen.append(title)
    return seen


def _parse_cursor(cursor: str | None) -> tuple[int | None, str | None]:
    """Resolve a continuation cursor to a start index.

    Returns (start_index, error). An absent cursor is (None, None); a malformed
    cursor is a hard error so a bad value is not silently treated as a restart
    from the beginning.
    """
    if not cursor:
        return None, None
    value = cursor.removeprefix("block:")
    try:
        return int(value), None
    except ValueError:
        return None, f"invalid cursor: {cursor!r}"


def _int_key_map(value: dict[str, Any]) -> dict[int, int]:
    result = {}
    for key, item in value.items():
        try:
            result[int(key)] = int(item)
        except (TypeError, ValueError):
            continue
    return result


def _str_key_map(value: dict[str, Any]) -> dict[int, str]:
    result = {}
    for key, item in value.items():
        try:
            result[int(key)] = str(item)
        except (TypeError, ValueError):
            continue
    return result


def _images_for_pages(config: dict, book_id: int, pages: set[int]) -> list[dict]:
    if not pages:
        return []
    from librarian import images

    catalog = images.list_book_images(config, book_id).get("images", [])
    linked = []
    for image in catalog:
        marker_page = image.get("marker_page")
        page = image.get("page")
        if marker_page in pages or page in pages:
            linked.append({
                "image_id": image.get("image_id"),
                "asset_type": image.get("asset_type"),
                "kind": image.get("kind"),
                "source": image.get("source"),
                "url": image.get("url"),
                "content_type": image.get("content_type"),
                "page": image.get("page"),
                "marker_page": image.get("marker_page"),
                "label": image.get("label"),
                "caption": image.get("caption"),
                "block_type": image.get("block_type"),
                "bbox": image.get("bbox"),
            })
    return linked
