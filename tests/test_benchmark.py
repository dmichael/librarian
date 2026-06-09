"""Benchmark tests using synthetic fixtures.

These tests use committed fixture data to verify retrieval quality
in a reproducible way, suitable for CI/CD.
"""

import tempfile
from pathlib import Path

import pytest

# Skip if dependencies not available
pytest.importorskip("llama_index")

from librarian.benchmark import BenchmarkQuery, score_result, run_benchmarks


def _bge_model_cached() -> bool:
    """True if the embedding model is already in the local HF cache.

    The fixture benchmarks embed real text; without a cached model they
    would download ~400MB mid-test, so they skip instead.
    """
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    return any(cache.glob("models--BAAI--bge-base-en-v1.5*"))


# Fixture-specific benchmark queries with known expected results
FIXTURE_BENCHMARKS = [
    # Investment basics
    BenchmarkQuery(
        query="What is an expense ratio?",
        expected_sources=["investment_basics"],
        expected_terms=["expense", "ratio", "fee", "percentage"],
        description="Basic finance concept",
    ),
    BenchmarkQuery(
        query="How does portfolio diversification reduce risk?",
        expected_sources=["investment_basics"],
        expected_terms=["diversification", "risk", "asset"],
        description="Portfolio theory",
    ),
    BenchmarkQuery(
        query="What is the Sharpe ratio formula?",
        expected_sources=["investment_basics"],
        expected_terms=["sharpe", "return", "risk"],
        description="Finance equation retrieval",
    ),

    # Mindfulness practice
    BenchmarkQuery(
        query="How do I practice breath awareness meditation?",
        expected_sources=["mindfulness_practice"],
        expected_terms=["breath", "attention", "focus"],
        description="Meditation technique",
    ),
    BenchmarkQuery(
        query="What is the RAIN technique for emotions?",
        expected_sources=["mindfulness_practice"],
        expected_terms=["rain", "emotion", "recognize"],
        description="Emotional regulation technique",
    ),
    BenchmarkQuery(
        query="How can I be more mindful in daily activities?",
        expected_sources=["mindfulness_practice"],
        expected_terms=["mindful", "daily", "practice"],
        description="Informal mindfulness",
    ),

    # Wave mechanics
    BenchmarkQuery(
        query="What is the equation for simple harmonic motion?",
        expected_sources=["wave_mechanics"],
        expected_terms=["harmonic", "oscillat", "equation"],
        description="Physics equation",
    ),
    BenchmarkQuery(
        query="What causes damping in oscillating systems?",
        expected_sources=["wave_mechanics"],
        expected_terms=["damp", "friction", "energy"],
        description="Damped oscillation concept",
    ),
    BenchmarkQuery(
        query="When does resonance occur?",
        expected_sources=["wave_mechanics"],
        expected_terms=["resonance", "frequency", "amplitude"],
        description="Resonance phenomenon",
    ),

    # Cross-domain (should find correct source)
    BenchmarkQuery(
        query="annual fees for investment funds",
        expected_sources=["investment_basics"],
        expected_terms=["expense", "fee", "fund"],
        description="Cross-domain: finance",
    ),
    BenchmarkQuery(
        query="dealing with difficult emotions",
        expected_sources=["mindfulness_practice"],
        expected_terms=["emotion", "mindful"],
        description="Cross-domain: psychology",
    ),
    BenchmarkQuery(
        query="differential equation for oscillation",
        expected_sources=["wave_mechanics"],
        expected_terms=["equation", "oscillat"],
        description="Cross-domain: physics",
    ),
]


@pytest.mark.skipif(not _bge_model_cached(), reason="BGE embedding model not in local HF cache")
class TestFixtureBenchmarks:
    """Benchmark tests using synthetic fixture data."""

    @pytest.fixture(scope="class")
    def indexed_store(self):
        """Index fixture documents into a temporary vectorstore."""
        from llama_index.core import Document, Settings, VectorStoreIndex
        from llama_index.core.node_parser import SentenceSplitter

        from librarian.embeddings import get_embed_model
        from librarian.vectorstore.qdrant_file import QdrantFileStore

        Settings.embed_model = get_embed_model(
            {"embedding": {"model": "BAAI/bge-base-en-v1.5", "device": "cpu"}}
        )

        # Create temp directory for vectorstore
        with tempfile.TemporaryDirectory() as tmpdir:
            store = QdrantFileStore(path=Path(tmpdir), default_collection="test_fixtures")

            # Load fixture documents
            fixtures_dir = Path(__file__).parent / "fixtures" / "books"
            documents = []
            for md_file in fixtures_dir.glob("*.md"):
                content = md_file.read_text()
                doc = Document(
                    text=content,
                    metadata={
                        "title": md_file.stem,
                        "source": str(md_file),
                    },
                )
                documents.append(doc)

            assert len(documents) == 3, f"Expected 3 fixture docs, got {len(documents)}"

            # Parse into chunks
            parser = SentenceSplitter(chunk_size=512, chunk_overlap=50)
            nodes = parser.get_nodes_from_documents(documents)

            # Index into vectorstore
            llama_store = store.get_llama_store("test_fixtures")
            index = VectorStoreIndex.from_vector_store(llama_store)
            index.insert_nodes(nodes)

            yield {
                "store": store,
                "collection": "test_fixtures",
                "node_count": len(nodes),
            }

    def test_fixtures_indexed(self, indexed_store):
        """Verify fixtures were indexed successfully."""
        assert indexed_store["node_count"] > 0
        assert indexed_store["store"].collection_exists("test_fixtures")

    @pytest.mark.parametrize("benchmark", FIXTURE_BENCHMARKS, ids=lambda b: b.description)
    def test_benchmark_query(self, indexed_store, benchmark):
        """Test individual benchmark query against fixtures."""
        from llama_index.core import Settings, VectorStoreIndex
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding

        # Ensure embedding model is set
        if Settings.embed_model is None:
            Settings.embed_model = HuggingFaceEmbedding(
                model_name="BAAI/bge-base-en-v1.5",
                device="cpu",
                query_instruction="Represent this sentence for searching relevant passages: ",
            )

        store = indexed_store["store"]
        collection = indexed_store["collection"]

        # Create retriever
        llama_store = store.get_llama_store(collection)
        index = VectorStoreIndex.from_vector_store(llama_store)
        retriever = index.as_retriever(similarity_top_k=5)

        # Retrieve and score
        nodes = retriever.retrieve(benchmark.query)
        result = score_result(benchmark, nodes, top_k=5)

        # Assertions with helpful messages
        assert result.recall >= 0.5, (
            f"Recall too low for '{benchmark.query}': {result.recall:.2f}\n"
            f"Expected sources: {benchmark.expected_sources}\n"
            f"Found sources: {result.found_sources}"
        )
        assert result.term_coverage >= 0.5, (
            f"Term coverage too low for '{benchmark.query}': {result.term_coverage:.2f}\n"
            f"Missing terms: {result.missing_terms}"
        )
        assert result.mrr > 0, (
            f"No relevant results in top-5 for '{benchmark.query}'"
        )

    def test_aggregate_metrics(self, indexed_store):
        """Test that aggregate metrics meet minimum thresholds."""
        from llama_index.core import Settings, VectorStoreIndex
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding

        if Settings.embed_model is None:
            Settings.embed_model = HuggingFaceEmbedding(
                model_name="BAAI/bge-base-en-v1.5",
                device="cpu",
                query_instruction="Represent this sentence for searching relevant passages: ",
            )

        store = indexed_store["store"]
        collection = indexed_store["collection"]

        llama_store = store.get_llama_store(collection)
        index = VectorStoreIndex.from_vector_store(llama_store)
        retriever = index.as_retriever(similarity_top_k=5)

        results = []
        for benchmark in FIXTURE_BENCHMARKS:
            nodes = retriever.retrieve(benchmark.query)
            result = score_result(benchmark, nodes, top_k=5)
            results.append(result)

        # Aggregate metrics
        mean_recall = sum(r.recall for r in results) / len(results)
        mean_term_cov = sum(r.term_coverage for r in results) / len(results)
        mean_mrr = sum(r.mrr for r in results) / len(results)

        # Thresholds for passing
        assert mean_recall >= 0.8, f"Mean recall {mean_recall:.2f} below threshold 0.8"
        assert mean_term_cov >= 0.7, f"Mean term coverage {mean_term_cov:.2f} below threshold 0.7"
        assert mean_mrr >= 0.5, f"Mean MRR {mean_mrr:.2f} below threshold 0.5"


class TestScoringFunctions:
    """Unit tests for benchmark scoring functions."""

    def test_perfect_score(self):
        """Test scoring with perfect results."""
        from llama_index.core.schema import TextNode, NodeWithScore

        benchmark = BenchmarkQuery(
            query="test query",
            expected_sources=["test_source"],
            expected_terms=["foo", "bar"],
            description="test",
        )

        # Create mock results that match perfectly
        nodes = [
            NodeWithScore(
                node=TextNode(
                    text="This text contains foo and bar.",
                    metadata={"title": "test_source_document"},
                ),
                score=0.9,
            )
        ]

        result = score_result(benchmark, nodes, top_k=5)

        assert result.recall == 1.0
        assert result.term_coverage == 1.0
        assert result.mrr == 1.0

    def test_no_match_score(self):
        """Test scoring with no matching results."""
        from llama_index.core.schema import TextNode, NodeWithScore

        benchmark = BenchmarkQuery(
            query="test query",
            expected_sources=["expected_source"],
            expected_terms=["expected_term"],
            description="test",
        )

        # Create mock results that don't match
        nodes = [
            NodeWithScore(
                node=TextNode(
                    text="Completely unrelated content.",
                    metadata={"title": "wrong_source"},
                ),
                score=0.5,
            )
        ]

        result = score_result(benchmark, nodes, top_k=5)

        assert result.recall == 0.0
        assert result.term_coverage == 0.0

    def test_partial_term_match(self):
        """Test scoring with partial term matches."""
        from llama_index.core.schema import TextNode, NodeWithScore

        benchmark = BenchmarkQuery(
            query="test query",
            expected_sources=["source"],
            expected_terms=["alpha", "beta", "gamma", "delta"],
            description="test",
        )

        nodes = [
            NodeWithScore(
                node=TextNode(
                    text="Contains alpha and beta but not the others.",
                    metadata={"title": "source_doc"},
                ),
                score=0.8,
            )
        ]

        result = score_result(benchmark, nodes, top_k=5)

        assert result.term_coverage == 0.5  # 2 of 4 terms
        assert result.recall == 1.0  # Source matches
