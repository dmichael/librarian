"""Shared metadata types and helpers for indexing and retrieval."""

from __future__ import annotations

import json
from typing import Any, Final, Mapping, TypedDict, cast

# Canonical metadata keys used in vector store payloads.
META_BOOK_ID: Final[str] = "book_id"
META_TITLE: Final[str] = "title"
META_AUTHORS: Final[str] = "authors"
META_TAGS: Final[str] = "tags"
META_PUBLISHER: Final[str] = "publisher"
META_SUBJECTS: Final[str] = "subjects"
META_LIBRARY: Final[str] = "library"
META_SOURCE_PATH: Final[str] = "source_path"
META_PAGE: Final[str] = "page"
META_BLOCK_TYPE: Final[str] = "block_type"
META_BLOCK_INDEX: Final[str] = "_block_idx"
META_CHAPTER_NUM: Final[str] = "chapter_num"
META_CHAPTER_TITLE: Final[str] = "chapter_title"
META_SECTION_TITLE: Final[str] = "section_title"
META_SECTION_TITLES: Final[str] = "section_titles"
META_BREADCRUMB: Final[str] = "breadcrumb"
META_START_PAGE: Final[str] = "start_page"
META_RESULT_TYPE: Final[str] = "_result_type"
META_LEVEL: Final[str] = "level"  # summary granularity: book | chapter | section

# Raw source metadata field used by calibre/mcp payloads.
SOURCE_LIBRARY_FIELD: Final[str] = "*library"


SourceBookMetadata = TypedDict(
    "SourceBookMetadata",
    {
        "id": int,
        "title": str,
        "authors": list[str],
        "tags": list[str],
        "publisher": str,
        "subjects": list[str],
        "source_path": str,
        "*library": str,
    },
    total=False,
)


class BaseNodeMetadata(TypedDict):
    """Shared metadata attached to indexed text nodes."""

    book_id: int
    title: str
    authors: str
    tags: str
    publisher: str
    subjects: str
    library: str
    source_path: str


class BlockNodeMetadata(BaseNodeMetadata):
    """Per-block metadata for chunk-level indexed nodes."""

    page: int | None
    block_type: str
    _block_idx: int


class ChapterNodeMetadata(TypedDict):
    """Summary-node metadata stored in the chapters collection.

    level distinguishes summary granularity: "book" (whole-book overview),
    "chapter", or "section" (books organized in sections without chapters).
    chapter_num is None for book- and section-level nodes.
    """

    book_id: int
    title: str
    chapter_num: int | None
    chapter_title: str
    summary: str
    page_range: str
    section_titles: str
    subjects: str
    library: str
    level: str


class SearchResultRow(TypedDict):
    """Result row shape returned by semantic search tool."""

    text: str
    score: float
    title: str
    authors: str
    book_id: int | None
    page: int | None
    chapter_num: int | None
    chapter_title: str
    block_type: str
    images: list[dict]


class TextSearchResultRow(TypedDict):
    """Result row shape returned by literal text search tool."""

    text: str
    title: str
    authors: str
    book_id: int | None
    page: int | None
    chapter_num: int | None
    chapter_title: str
    library: str


def serialize_list_metadata(value: Any) -> Any:
    """Serialize list values to JSON strings for backend compatibility."""
    if isinstance(value, list):
        return json.dumps(value)
    return value


def normalize_subject_filter(subject: str) -> str:
    """Normalize subject filter values (expand wildcard prefix syntax)."""
    if subject.endswith("/*"):
        return subject[:-2]
    return subject


def build_base_node_metadata(
    *,
    book_id: int,
    metadata: Mapping[str, Any],
) -> BaseNodeMetadata:
    """Build core node metadata from source metadata."""
    subjects = metadata.get("subjects", [])
    tags = metadata.get("tags", [])
    library = metadata.get(SOURCE_LIBRARY_FIELD, "") or ""

    return {
        META_BOOK_ID: book_id,
        META_TITLE: metadata.get("title", "Unknown"),
        META_AUTHORS: ", ".join(metadata.get("authors", [])),
        META_TAGS: serialize_list_metadata(tags),
        META_PUBLISHER: metadata.get("publisher", ""),
        META_SUBJECTS: serialize_list_metadata(subjects),
        META_LIBRARY: library,
        META_SOURCE_PATH: metadata.get("source_path", ""),
    }


def with_block_metadata(
    base_metadata: BaseNodeMetadata,
    *,
    page: int | None,
    block_type: str,
    block_idx: int,
) -> BlockNodeMetadata:
    """Add per-block fields to shared node metadata."""
    node_meta: dict[str, Any] = dict(base_metadata)
    node_meta[META_PAGE] = page
    node_meta[META_BLOCK_TYPE] = block_type
    node_meta[META_BLOCK_INDEX] = block_idx
    return cast(BlockNodeMetadata, node_meta)


def build_chapter_node_metadata(
    *,
    metadata: Mapping[str, Any],
    chapter_num: int | None,
    chapter_title: str,
    summary: str,
    page_range: str,
    section_titles: list[str],
    level: str = "chapter",
) -> ChapterNodeMetadata:
    """Build metadata for summary nodes (book/chapter/section level)."""
    return {
        META_BOOK_ID: metadata.get("id", 0),
        META_TITLE: metadata.get("title", "Unknown"),
        META_CHAPTER_NUM: chapter_num,
        META_CHAPTER_TITLE: chapter_title,
        "summary": summary,
        "page_range": page_range,
        "section_titles": serialize_list_metadata(section_titles),
        META_SUBJECTS: serialize_list_metadata(metadata.get("subjects", [])),
        META_LIBRARY: metadata.get(SOURCE_LIBRARY_FIELD, "") or "",
        META_LEVEL: level,
    }


def build_search_result_row(
    *,
    text: str,
    score: float,
    metadata: Mapping[str, Any],
    images: list[dict] | None = None,
) -> SearchResultRow:
    """Build semantic-search response row from node metadata."""
    return {
        "text": text,
        "score": round(score, 4),
        "title": metadata.get(META_TITLE, "Unknown"),
        "authors": metadata.get(META_AUTHORS, ""),
        "book_id": metadata.get(META_BOOK_ID),
        "page": metadata.get(META_PAGE),
        "chapter_num": metadata.get(META_CHAPTER_NUM),
        "chapter_title": metadata.get(META_CHAPTER_TITLE, ""),
        "block_type": metadata.get(META_BLOCK_TYPE, ""),
        "images": images or [],
    }


def build_text_search_result_row(
    *,
    text: str,
    metadata: Mapping[str, Any],
) -> TextSearchResultRow:
    """Build literal text-search response row from metadata."""
    return {
        "text": text,
        "title": metadata.get(META_TITLE, "Unknown"),
        "authors": metadata.get(META_AUTHORS, ""),
        "book_id": metadata.get(META_BOOK_ID),
        "page": metadata.get(META_PAGE),
        "chapter_num": metadata.get(META_CHAPTER_NUM),
        "chapter_title": metadata.get(META_CHAPTER_TITLE, ""),
        "library": metadata.get(META_LIBRARY, ""),
    }
