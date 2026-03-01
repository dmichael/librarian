"""Characterization tests for indexing metadata contract.

These tests intentionally validate current behavior before typed-contract
refactors, so regressions are visible during migration.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest

# Test-only shim: allow importing librarian.index in minimal dev environments
# where optional latex dependency is not installed.
if importlib.util.find_spec("pylatexenc") is None:
    pylatexenc_mod = types.ModuleType("pylatexenc")
    latex2text_mod = types.ModuleType("pylatexenc.latex2text")

    class _DummyLatexNodes2Text:
        def latex_to_text(self, value: str) -> str:
            return value

    latex2text_mod.LatexNodes2Text = _DummyLatexNodes2Text
    sys.modules["pylatexenc"] = pylatexenc_mod
    sys.modules["pylatexenc.latex2text"] = latex2text_mod

from librarian.index import create_nodes_from_blocks


class TestIndexingMetadataContract(unittest.TestCase):
    def test_create_nodes_from_blocks_sets_core_metadata_fields(self) -> None:
        blocks = [
            {"text": "Chapter intro", "page": 12, "block_type": "Text"},
            {"text": "print(42)", "page": 13, "block_type": "Code"},
        ]
        metadata = {
            "title": "Test Source",
            "authors": ["Ada Lovelace", "Grace Hopper"],
            "subjects": ["cs", "cs/history"],
            "tags": ["foundational"],
            "*library": "engineering-core",
            "source_path": "/data/librarian/intake/ebooks/test.pdf",
            "publisher": "Librarian Press",
        }

        nodes = create_nodes_from_blocks(blocks, book_id=101, metadata=metadata, chunk_size=512)

        self.assertEqual(len(nodes), 2)
        for i, node in enumerate(nodes):
            meta = node.metadata
            self.assertEqual(meta["book_id"], 101)
            self.assertEqual(meta["title"], "Test Source")
            self.assertEqual(meta["authors"], "Ada Lovelace, Grace Hopper")
            self.assertEqual(meta["library"], "engineering-core")
            self.assertEqual(meta["source_path"], "/data/librarian/intake/ebooks/test.pdf")
            self.assertIn("block_type", meta)
            self.assertIn("_block_idx", meta)
            self.assertEqual(meta["_block_idx"], i)

            # Current behavior: list metadata is JSON-serialized.
            self.assertEqual(json.loads(meta["subjects"]), ["cs", "cs/history"])
            self.assertEqual(json.loads(meta["tags"]), ["foundational"])

    def test_large_text_block_is_split_but_block_identity_is_preserved(self) -> None:
        long_text = "\n\n".join([f"Paragraph {i} " + ("x" * 100) for i in range(20)])
        blocks = [{"text": long_text, "page": 7, "block_type": "Text"}]
        metadata = {
            "title": "Split Test",
            "authors": ["One Author"],
            "subjects": [],
            "tags": [],
            "*library": "test-lib",
            "source_path": "/tmp/source.pdf",
        }

        nodes = create_nodes_from_blocks(blocks, book_id=5, metadata=metadata, chunk_size=256)

        self.assertGreater(len(nodes), 1)
        for node in nodes:
            meta = node.metadata
            self.assertEqual(meta["book_id"], 5)
            self.assertEqual(meta["page"], 7)
            self.assertEqual(meta["block_type"], "Text")
            # Current behavior: all splits from one block share block index 0.
            self.assertEqual(meta["_block_idx"], 0)


if __name__ == "__main__":
    unittest.main()
