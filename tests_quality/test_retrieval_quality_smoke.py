"""Focused retrieval quality smoke tests with fixed query expectations.

These tests index committed fixture books into a local temporary Qdrant store
with deterministic keyword embeddings. The goal is not benchmark-grade scoring;
it is regression detection for retrieval wiring and metadata flow.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from llama_index.core import Document, Settings, VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.node_parser import SentenceSplitter

from librarian.vectorstore.qdrant_file import QdrantFileStore

_TOKEN_RE = re.compile(r"[a-z]+")
_CHAPTER_RE = re.compile(
    r"^## Chapter (\d+): (.+?)\n(.*?)(?=^## Chapter \d+: |\Z)",
    flags=re.MULTILINE | re.DOTALL,
)

# Small fixed vocabulary for deterministic lexical-semantic matching.
_VOCAB = [
    "expense", "ratio", "fund", "fee", "mutual", "portfolio", "risk",
    "breath", "meditation", "rain", "emotion", "mindful", "practice",
    "oscillator", "harmonic", "damped", "resonance", "equation",
]


class _KeywordEmbedding(BaseEmbedding):
    """Simple token-count embedding for deterministic local tests."""

    vocab: list[str]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * len(self.vocab)
        index = {token: i for i, token in enumerate(self.vocab)}
        for token in _TOKEN_RE.findall(text.lower()):
            idx = index.get(token)
            if idx is not None:
                vec[idx] += 1.0
        return vec

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed(text)


def _build_chapter_documents(fixtures_dir: Path) -> list[Document]:
    docs: list[Document] = []
    for md_file in fixtures_dir.glob("*.md"):
        content = md_file.read_text()
        title = md_file.stem
        for match in _CHAPTER_RE.finditer(content):
            chapter_num = int(match.group(1))
            chapter_title = match.group(2).strip()
            chapter_body = match.group(3).strip()
            chapter_text = f"Chapter {chapter_num}: {chapter_title}\n\n{chapter_body}"
            docs.append(
                Document(
                    text=chapter_text,
                    metadata={
                        "title": title,
                        "chapter_num": chapter_num,
                        "chapter_title": chapter_title,
                    },
                )
            )
    return docs


class TestRetrievalQualitySmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Settings.embed_model = _KeywordEmbedding(vocab=_VOCAB, embed_dim=len(_VOCAB))
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._store = QdrantFileStore(path=Path(cls._tmpdir.name), default_collection="quality_smoke")
        cls._collection = "quality_smoke"

        fixtures_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "books"
        docs = _build_chapter_documents(fixtures_dir)
        parser = SentenceSplitter(chunk_size=320, chunk_overlap=40)
        nodes = parser.get_nodes_from_documents(docs)

        llama_store = cls._store.get_llama_store(cls._collection)
        index = VectorStoreIndex.from_vector_store(llama_store)
        index.insert_nodes(nodes)
        cls._retriever = index.as_retriever(similarity_top_k=5)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def test_fixed_queries_hit_expected_book_and_chapter(self) -> None:
        cases = [
            ("expense ratio mutual fund fee", "investment_basics", 1),
            ("RAIN technique difficult emotions", "mindfulness_practice", 3),
            ("damped oscillator differential equation", "wave_mechanics", 2),
        ]

        for query, expected_book, expected_chapter in cases:
            with self.subTest(query=query):
                nodes = self._retriever.retrieve(query)
                self.assertTrue(nodes, f"Expected retrieval results for query: {query}")

                top3_books = [n.metadata.get("title") for n in nodes[:3]]
                self.assertIn(
                    expected_book,
                    top3_books,
                    f"Expected book '{expected_book}' in top-3 for '{query}', got {top3_books}",
                )

                chapter_hit = any(
                    n.metadata.get("title") == expected_book
                    and n.metadata.get("chapter_num") == expected_chapter
                    for n in nodes[:5]
                )
                found = [
                    (n.metadata.get("title"), n.metadata.get("chapter_num"))
                    for n in nodes[:5]
                ]
                self.assertTrue(
                    chapter_hit,
                    f"Expected chapter {expected_chapter} for '{expected_book}' in top-5 for '{query}', got {found}",
                )


if __name__ == "__main__":
    unittest.main()
