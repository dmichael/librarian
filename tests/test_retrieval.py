"""Tests for RAG retrieval quality.

These tests verify that specific queries retrieve chunks containing
expected content. They serve as regression tests for retrieval improvements.
"""

import pytest
from pathlib import Path

# Skip all tests if dependencies not available
pytest.importorskip("llama_index")
pytest.importorskip("qdrant_client")


class TestLatexAugmentation:
    """Test that LaTeX equations are augmented with searchable text."""

    def test_augment_display_equations(self):
        from librarian.index import augment_latex_equations

        text = r"The equation is $$E = mc^2$$ which shows energy-mass equivalence."
        result = augment_latex_equations(text)

        # Should contain original equation
        assert r"$$E = mc^2$$" in result
        # Should contain augmentation marker
        assert "[Mathematical equation:" in result

    def test_augment_complex_equation(self):
        from librarian.index import augment_latex_equations

        # Syrinx oscillator equation from Gardner paper
        text = r"$$M\ddot{x} + D\dot{x} + D_2(\dot{x})^3 + Kx = P_b$$"
        result = augment_latex_equations(text)

        assert "[Mathematical equation:" in result
        # pylatexenc should convert to readable form
        assert "M" in result

    def test_no_augmentation_for_inline_math(self):
        from librarian.index import augment_latex_equations

        # Only display math ($$...$$) should be augmented, not inline ($...$)
        text = r"The variable $x$ represents position."
        result = augment_latex_equations(text)

        # Should be unchanged (no display equations)
        assert result == text


class TestBuildFilters:
    """Unit tests for metadata filter construction."""

    def _flatten_keys(self, filters):
        """Collect all filter keys, descending into nested MetadataFilters."""
        from llama_index.core.vector_stores import MetadataFilters

        keys = []
        for f in filters.filters:
            if isinstance(f, MetadataFilters):
                keys.extend(self._flatten_keys(f))
            else:
                keys.append(f.key)
        return keys

    def test_no_filters_returns_none(self):
        from librarian.query import _build_filters

        assert _build_filters(None, None, None) is None

    def test_block_type_kept_with_subjects(self):
        # Regression: block_type used to be dropped when subjects were set
        # without library.
        from librarian.query import _build_filters
        from librarian.metadata_types import META_BLOCK_TYPE, META_SUBJECTS

        filters = _build_filters(["psychology/*"], None, "Code")
        keys = self._flatten_keys(filters)

        assert META_BLOCK_TYPE in keys
        assert META_SUBJECTS in keys

    def test_library_subjects_and_block_type_all_kept(self):
        from librarian.query import _build_filters
        from librarian.metadata_types import (
            META_BLOCK_TYPE,
            META_LIBRARY,
            META_SUBJECTS,
        )

        filters = _build_filters(["finance/*"], "therapy", "Table")
        keys = self._flatten_keys(filters)

        assert META_LIBRARY in keys
        assert META_BLOCK_TYPE in keys
        assert META_SUBJECTS in keys
        assert filters.condition == "and"

    def test_subjects_only_is_or_group(self):
        from llama_index.core.vector_stores import MetadataFilters

        from librarian.query import _build_filters

        filters = _build_filters(["a/*", "b/*"], None, None)

        # One nested OR group containing both subject filters
        assert filters.condition == "and"
        assert len(filters.filters) == 1
        inner = filters.filters[0]
        assert isinstance(inner, MetadataFilters)
        assert inner.condition == "or"
        assert len(inner.filters) == 2


class TestRetrievalQuality:
    """Integration tests for retrieval quality.

    These tests require a populated Qdrant index. They are marked as
    integration tests and can be skipped in CI with: pytest -m "not integration"
    """

    @pytest.fixture
    def config(self):
        """Load config for tests."""
        from librarian.config import load_config, expand_path

        config = load_config()
        vs_config = config.get("vector_store", {})
        path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))

        if not path.exists():
            pytest.skip("Qdrant index not found")

        return config

    @pytest.fixture
    def retriever(self, config):
        """Set up retriever connected to Qdrant."""
        from librarian.config import expand_path
        from llama_index.core import Settings
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding
        from llama_index.vector_stores.qdrant import QdrantVectorStore
        from llama_index.core import VectorStoreIndex
        from qdrant_client import QdrantClient

        vs_config = config.get("vector_store", {})
        path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))
        collection = vs_config.get("collection", "librarian_full")

        # Set up embedding model
        embed_model = HuggingFaceEmbedding(
            model_name="BAAI/bge-base-en-v1.5",
            device="cpu",  # Use CPU for tests
            query_instruction="Represent this sentence for searching relevant passages: ",
        )
        Settings.embed_model = embed_model

        client = QdrantClient(path=str(path))
        vector_store = QdrantVectorStore(client=client, collection_name=collection)
        index = VectorStoreIndex.from_vector_store(vector_store)

        return index.as_retriever(similarity_top_k=10)

    @pytest.mark.integration
    def test_birdsong_oscillator_equation_retrieval(self, retriever):
        """Test that querying for syrinx oscillator retrieves equation (4).

        The main oscillator equation is:
        M*x'' + D*x' + D_2*(x')^3 + K*x = P_b * (a_0 - b_0 + 2*tau*x') / (x + b_0 + tau*x')

        A successful retrieval should return a chunk containing this equation
        or its augmented description.
        """
        queries = [
            "syrinx oscillator differential equation",
            "damped harmonic oscillator nonlinear dissipation birdsong",
            "labial mass equation pressure",
        ]

        for query in queries:
            nodes = retriever.retrieve(query)
            texts = [n.node.text for n in nodes]
            combined = " ".join(texts)

            # Check if any retrieved chunk contains the main equation components
            has_equation = any([
                "Mẍ" in combined or r"M\ddot{x}" in combined,
                "nonlinear dissipation" in combined.lower(),
                "D_2" in combined and "x" in combined,
            ])

            if has_equation:
                return  # Success - found equation in at least one query

        pytest.fail(
            f"None of the queries retrieved chunks containing the main "
            f"oscillator equation. Retrieved texts: {texts[:2]}"
        )

    @pytest.mark.integration
    def test_retrieval_returns_correct_book(self, retriever):
        """Test that book metadata is preserved in retrieval."""
        nodes = retriever.retrieve("birdsong syrinx model")

        # Should retrieve from Gardner paper
        book_ids = [n.node.metadata.get("book_id") for n in nodes]
        titles = [n.node.metadata.get("title", "") for n in nodes]

        assert any("Gardner" in t or "Birdsong" in t for t in titles), (
            f"Expected Gardner birdsong paper, got titles: {titles}"
        )


class TestDualRetrieval:
    """Test dual retrieval from text and equation collections."""

    @pytest.fixture
    def config(self):
        """Load config for tests."""
        from librarian.config import load_config, expand_path

        config = load_config()
        vs_config = config.get("vector_store", {})
        path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))

        if not path.exists():
            pytest.skip("Qdrant index not found")

        return config

    @pytest.mark.integration
    def test_retrieve_includes_equations(self, config):
        """Test that retrieve() returns both text and equation results."""
        from librarian.query import retrieve

        nodes = retrieve(
            "differential equation oscillator",
            config,
            top_k=5,
            include_equations=True,
            equation_top_k=3,
        )

        result_types = [n.metadata.get("_result_type") for n in nodes]

        # Should have both types
        assert "equation" in result_types, "Expected equations in results"
        assert "text" in result_types, "Expected text chunks in results"

    @pytest.mark.integration
    def test_equation_metadata_complete(self, config):
        """Test that equation results have complete metadata."""
        from librarian.query import retrieve_equations_only

        nodes = retrieve_equations_only("oscillator equation", config, top_k=5)

        if not nodes:
            pytest.skip("No equations indexed")

        for node in nodes:
            meta = node.node.metadata
            # Required equation metadata
            assert "latex" in meta, "Equation missing latex"
            assert "description" in meta, "Equation missing description"
            assert "context_window" in meta, "Equation missing context_window"
            assert meta.get("type") == "equation", "Equation has wrong type"

    @pytest.mark.integration
    def test_equation_retrieval_finds_oscillator(self, config):
        """Test that querying for oscillator finds the main equation."""
        from librarian.query import retrieve_equations_only

        nodes = retrieve_equations_only(
            "damped harmonic oscillator nonlinear dissipation",
            config,
            top_k=5,
        )

        if not nodes:
            pytest.skip("No equations indexed")

        # Look for equation 4 (the main oscillator)
        found_oscillator = False
        for node in nodes:
            latex = node.node.metadata.get("latex", "")
            if r"\ddot{x}" in latex and "D_2" in latex:
                found_oscillator = True
                break

        assert found_oscillator, (
            "Expected to find main oscillator equation with \\ddot{x} and D_2"
        )

    @pytest.mark.integration
    def test_can_disable_equation_retrieval(self, config):
        """Test that include_equations=False skips equation collection."""
        from librarian.query import retrieve

        nodes = retrieve(
            "oscillator equation",
            config,
            top_k=5,
            include_equations=False,
        )

        result_types = [n.metadata.get("_result_type") for n in nodes]

        # Should only have text results
        assert "equation" not in result_types, "Should not have equations when disabled"


class TestChunkingQuality:
    """Test that equations stay with their surrounding context."""

    def test_equation_not_isolated(self):
        """Equations should not be chunked into isolation."""
        from librarian.index import augment_latex_equations
        from llama_index.core.node_parser import SentenceSplitter
        from llama_index.core import Document

        # Simulate a document with equation and context
        text = """
        The oscillator is described by the following equation:

        $$M\\ddot{x} + D\\dot{x} + Kx = F$$

        where M is mass, D is damping, K is stiffness, and F is the driving force.
        This equation governs the labial dynamics in the syrinx.
        """

        augmented = augment_latex_equations(text)
        doc = Document(text=augmented)

        # Use default chunking settings
        parser = SentenceSplitter(chunk_size=512, chunk_overlap=50)
        nodes = parser.get_nodes_from_documents([doc])

        # Find the chunk with the equation
        equation_chunks = [n for n in nodes if "$$" in n.text or "Mathematical equation" in n.text]

        assert len(equation_chunks) > 0, "Equation should appear in at least one chunk"

        # The chunk with the equation should also have context
        for chunk in equation_chunks:
            has_context = (
                "mass" in chunk.text.lower() or
                "damping" in chunk.text.lower() or
                "oscillator" in chunk.text.lower()
            )
            assert has_context, (
                f"Equation chunk lacks surrounding context: {chunk.text[:200]}"
            )
