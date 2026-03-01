"""Characterization tests for shared metadata type helpers."""

from __future__ import annotations

import json
import unittest

from librarian.metadata_types import (
    META_BLOCK_INDEX,
    META_BLOCK_TYPE,
    META_BOOK_ID,
    META_CHAPTER_NUM,
    META_CHAPTER_TITLE,
    META_LIBRARY,
    META_SUBJECTS,
    build_search_result_row,
    build_base_node_metadata,
    build_chapter_node_metadata,
    build_text_search_result_row,
    normalize_subject_filter,
    with_block_metadata,
)


class TestMetadataTypesContract(unittest.TestCase):
    def test_build_base_node_metadata_serializes_lists_and_sets_core_fields(self) -> None:
        source = {
            "title": "Typed Metadata",
            "authors": ["Ada", "Grace"],
            "tags": ["core", "baseline"],
            "publisher": "Librarian Press",
            "subjects": ["cs", "cs/history"],
            "*library": "engineering",
            "source_path": "/data/librarian/intake/ebooks/test.pdf",
        }

        meta = build_base_node_metadata(book_id=42, metadata=source)

        self.assertEqual(meta[META_BOOK_ID], 42)
        self.assertEqual(meta["title"], "Typed Metadata")
        self.assertEqual(meta["authors"], "Ada, Grace")
        self.assertEqual(meta[META_LIBRARY], "engineering")
        self.assertEqual(meta["source_path"], "/data/librarian/intake/ebooks/test.pdf")
        self.assertEqual(json.loads(meta["tags"]), ["core", "baseline"])
        self.assertEqual(json.loads(meta[META_SUBJECTS]), ["cs", "cs/history"])

    def test_with_block_metadata_adds_fields_without_mutating_base(self) -> None:
        base = build_base_node_metadata(
            book_id=7,
            metadata={"title": "Block Test", "authors": [], "subjects": [], "tags": []},
        )

        node_meta = with_block_metadata(base, page=13, block_type="Code", block_idx=5)

        self.assertEqual(node_meta[META_BOOK_ID], 7)
        self.assertEqual(node_meta[META_BLOCK_TYPE], "Code")
        self.assertEqual(node_meta[META_BLOCK_INDEX], 5)
        self.assertEqual(node_meta["page"], 13)
        # Base dict remains shared-only fields.
        self.assertNotIn(META_BLOCK_TYPE, base)
        self.assertNotIn(META_BLOCK_INDEX, base)
        self.assertNotIn("page", base)

    def test_build_chapter_node_metadata_and_subject_normalization(self) -> None:
        chapter_meta = build_chapter_node_metadata(
            metadata={"id": 9, "title": "Chapter Source", "subjects": ["therapy/dbt"], "*library": "therapy-core"},
            chapter_num=3,
            chapter_title="Distress Tolerance",
            summary="Skills for crisis moments.",
            page_range="45-72",
            section_titles=["What Is Distress Tolerance?", "TIP Skills"],
        )

        self.assertEqual(chapter_meta[META_BOOK_ID], 9)
        self.assertEqual(chapter_meta[META_CHAPTER_NUM], 3)
        self.assertEqual(chapter_meta[META_CHAPTER_TITLE], "Distress Tolerance")
        self.assertEqual(chapter_meta[META_LIBRARY], "therapy-core")
        self.assertEqual(json.loads(chapter_meta["section_titles"]), ["What Is Distress Tolerance?", "TIP Skills"])
        self.assertEqual(json.loads(chapter_meta[META_SUBJECTS]), ["therapy/dbt"])

        self.assertEqual(normalize_subject_filter("psychology/*"), "psychology")
        self.assertEqual(normalize_subject_filter("psychology/cbt"), "psychology/cbt")

    def test_build_search_result_row_uses_defaults_and_rounding(self) -> None:
        row = build_search_result_row(
            text="chunk text",
            score=0.987654,
            metadata={
                "book_id": 11,
                "title": "Source",
                "authors": "Ada",
                "page": 9,
                "chapter_num": 2,
                "chapter_title": "Intro",
                "block_type": "Text",
            },
        )
        self.assertEqual(row["text"], "chunk text")
        self.assertEqual(row["score"], 0.9877)
        self.assertEqual(row["book_id"], 11)
        self.assertEqual(row["title"], "Source")
        self.assertEqual(row["authors"], "Ada")
        self.assertEqual(row["page"], 9)
        self.assertEqual(row["chapter_num"], 2)
        self.assertEqual(row["chapter_title"], "Intro")
        self.assertEqual(row["block_type"], "Text")

        default_row = build_search_result_row(text="x", score=0.1, metadata={})
        self.assertEqual(default_row["title"], "Unknown")
        self.assertEqual(default_row["authors"], "")
        self.assertEqual(default_row["chapter_title"], "")
        self.assertEqual(default_row["block_type"], "")
        self.assertIsNone(default_row["book_id"])

    def test_build_text_search_result_row_uses_defaults(self) -> None:
        row = build_text_search_result_row(
            text="exact text",
            metadata={
                "book_id": 4,
                "title": "Exact Match",
                "authors": "Grace",
                "page": 77,
                "chapter_num": 5,
                "chapter_title": "Appendix",
                "library": "engineering",
            },
        )
        self.assertEqual(row["text"], "exact text")
        self.assertEqual(row["title"], "Exact Match")
        self.assertEqual(row["authors"], "Grace")
        self.assertEqual(row["book_id"], 4)
        self.assertEqual(row["page"], 77)
        self.assertEqual(row["chapter_num"], 5)
        self.assertEqual(row["chapter_title"], "Appendix")
        self.assertEqual(row["library"], "engineering")

        default_row = build_text_search_result_row(text="x", metadata={})
        self.assertEqual(default_row["title"], "Unknown")
        self.assertEqual(default_row["authors"], "")
        self.assertEqual(default_row["chapter_title"], "")
        self.assertEqual(default_row["library"], "")


if __name__ == "__main__":
    unittest.main()
