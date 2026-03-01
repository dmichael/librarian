"""Characterization tests for retrieval filter construction."""

from __future__ import annotations

import unittest

from llama_index.core.vector_stores.types import FilterCondition, FilterOperator

from librarian.query import _build_filters


class TestRetrievalFilterContract(unittest.TestCase):
    def test_no_filters_returns_none(self) -> None:
        self.assertIsNone(_build_filters(subjects=None, library=None, block_type=None))

    def test_library_only_filter(self) -> None:
        filters = _build_filters(subjects=None, library="therapy-core", block_type=None)
        self.assertIsNotNone(filters)
        self.assertEqual(filters.condition, FilterCondition.AND)
        self.assertEqual(len(filters.filters), 1)

        f = filters.filters[0]
        self.assertEqual(f.key, "library")
        self.assertEqual(f.value, "therapy-core")
        self.assertEqual(f.operator, FilterOperator.EQ)

    def test_library_and_block_type_filter(self) -> None:
        filters = _build_filters(subjects=None, library="therapy-core", block_type="Code")
        self.assertIsNotNone(filters)
        self.assertEqual(filters.condition, FilterCondition.AND)
        self.assertEqual(len(filters.filters), 2)

        by_key = {f.key: f for f in filters.filters}
        self.assertEqual(by_key["library"].operator, FilterOperator.EQ)
        self.assertEqual(by_key["block_type"].operator, FilterOperator.EQ)

    def test_subject_wildcard_with_library_builds_and_or_shape(self) -> None:
        filters = _build_filters(
            subjects=["therapy/*", "psychology/cbt"],
            library="therapy-core",
            block_type=None,
        )
        self.assertIsNotNone(filters)
        self.assertEqual(filters.condition, FilterCondition.AND)
        self.assertEqual(len(filters.filters), 2)

        # Current behavior: second element is nested OR MetadataFilters for subjects.
        nested = filters.filters[1]
        self.assertEqual(nested.condition, FilterCondition.OR)
        self.assertEqual(len(nested.filters), 2)

        self.assertEqual(nested.filters[0].key, "subjects")
        self.assertEqual(nested.filters[0].value, "therapy")
        self.assertEqual(nested.filters[0].operator, FilterOperator.TEXT_MATCH)

        self.assertEqual(nested.filters[1].key, "subjects")
        self.assertEqual(nested.filters[1].value, "psychology/cbt")
        self.assertEqual(nested.filters[1].operator, FilterOperator.TEXT_MATCH)


if __name__ == "__main__":
    unittest.main()
