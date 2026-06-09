"""Semantic search benchmarks for measuring retrieval efficacy.

This module provides:
- Benchmark queries with expected results
- Scoring metrics (precision@k, recall, MRR)
- Backend-agnostic runner using vectorstore abstraction
- CLI for comparing backends

Usage:
    librarian-benchmark                        # Run with current backend
    librarian-benchmark --backend qdrant-file  # Run with specific backend
    librarian-benchmark --compare              # Compare all available backends
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from librarian.config import load_config
from librarian.vectorstore import get_vector_store, get_collection_names


# =============================================================================
# Benchmark Query Definitions
# =============================================================================

@dataclass
class BenchmarkQuery:
    """A benchmark query with expected results."""
    query: str
    expected_sources: list[str]  # Substrings to match in title/book_id
    expected_terms: list[str]    # Terms that should appear in results
    collection: str = "full"     # full, equations, or chapters
    description: str = ""        # Human description of what we're testing


# Benchmark suite - add more queries here to expand coverage
BENCHMARK_QUERIES = [
    # Finance domain
    BenchmarkQuery(
        query="What is an expense ratio?",
        expected_sources=["Fund Industry"],
        expected_terms=["expense", "ratio", "fee"],
        description="Basic finance concept retrieval",
    ),
    BenchmarkQuery(
        query="How are hedge fund fees calculated?",
        expected_sources=["Fund Industry"],
        expected_terms=["fee", "performance", "management"],
        description="Specific finance topic",
    ),
    BenchmarkQuery(
        query="mutual fund performance evaluation",
        expected_sources=["Fund Industry"],
        expected_terms=["performance", "fund", "return"],
        collection="chapters",
        description="Chapter-level retrieval for finance",
    ),

    # Psychology/DBT domain
    BenchmarkQuery(
        query="How do I practice mindfulness?",
        expected_sources=["DBT", "Skills Training"],
        expected_terms=["attention", "present", "awareness", "practice"],
        description="Mindfulness techniques",
    ),
    BenchmarkQuery(
        query="emotion regulation techniques",
        expected_sources=["DBT", "Skills Training"],
        expected_terms=["emotion", "regulat"],
        description="DBT core skill",
    ),
    BenchmarkQuery(
        query="distress tolerance skills",
        expected_sources=["DBT", "Skills Training"],
        expected_terms=["distress", "tolerance", "crisis"],
        description="DBT distress tolerance module",
    ),

    # Science/birdsong domain
    BenchmarkQuery(
        query="How does the syrinx produce sound?",
        expected_sources=["Gardner", "Birdsong"],
        expected_terms=["syrinx", "vocal", "oscillat"],
        description="Birdsong mechanics",
    ),
    BenchmarkQuery(
        query="vocal fold oscillation model",
        expected_sources=["Gardner", "Birdsong"],
        expected_terms=["oscillat", "fold", "model"],
        description="Physical model of birdsong",
    ),
    BenchmarkQuery(
        query="damped harmonic oscillator equation",
        expected_sources=["Gardner"],
        expected_terms=["equation", "P_b"],  # Equation number marker and pressure term
        collection="equations",
        description="Equation retrieval for physics",
    ),

    # Cross-domain (should retrieve from appropriate source)
    BenchmarkQuery(
        query="investment portfolio management",
        expected_sources=["Fund Industry"],
        expected_terms=["portfolio", "invest", "fund"],
        description="Investment topic routing",
    ),
    BenchmarkQuery(
        query="interpersonal effectiveness",
        expected_sources=["DBT", "Skills Training"],
        expected_terms=["interpersonal", "relationship"],
        description="DBT interpersonal module",
    ),
]


# =============================================================================
# Scoring Metrics
# =============================================================================

@dataclass
class QueryResult:
    """Results for a single benchmark query."""
    query: str
    description: str
    precision_at_k: float      # % of top-k results that are relevant
    recall: float              # % of expected sources found
    term_coverage: float       # % of expected terms found in results
    mrr: float                 # Mean reciprocal rank of first relevant result
    top_k: int
    relevant_count: int
    expected_sources: list[str]
    found_sources: list[str]
    missing_terms: list[str]


@dataclass
class BenchmarkReport:
    """Aggregate benchmark results."""
    backend: str
    collection_names: dict[str, str]
    total_queries: int
    results: list[QueryResult]

    @property
    def mean_precision(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.precision_at_k for r in self.results) / len(self.results)

    @property
    def mean_recall(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.recall for r in self.results) / len(self.results)

    @property
    def mean_term_coverage(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.term_coverage for r in self.results) / len(self.results)

    @property
    def mean_mrr(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.mrr for r in self.results) / len(self.results)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "total_queries": self.total_queries,
            "mean_precision_at_k": round(self.mean_precision, 3),
            "mean_recall": round(self.mean_recall, 3),
            "mean_term_coverage": round(self.mean_term_coverage, 3),
            "mean_mrr": round(self.mean_mrr, 3),
            "results": [
                {
                    "query": r.query,
                    "description": r.description,
                    "precision_at_k": round(r.precision_at_k, 3),
                    "recall": round(r.recall, 3),
                    "term_coverage": round(r.term_coverage, 3),
                    "mrr": round(r.mrr, 3),
                    "found_sources": r.found_sources,
                    "missing_terms": r.missing_terms,
                }
                for r in self.results
            ],
        }


def score_result(
    benchmark: BenchmarkQuery,
    retrieved_nodes: list,
    top_k: int = 5,
) -> QueryResult:
    """Score retrieval results against expected outcomes."""
    # Extract text and metadata from results
    texts = []
    sources = []
    for node in retrieved_nodes[:top_k]:
        if hasattr(node, "node"):
            # NodeWithScore wrapper
            text = node.node.text
            meta = node.node.metadata
        else:
            text = node.text
            meta = node.metadata
        texts.append(text.lower())
        title = meta.get("title", "") or meta.get("book_title", "") or ""
        sources.append(title)

    combined_text = " ".join(texts)

    # Calculate relevance for each result
    relevant_mask = []
    for i, (text, source) in enumerate(zip(texts, sources)):
        # A result is relevant if it matches expected source OR contains expected terms
        source_match = any(
            exp.lower() in source.lower()
            for exp in benchmark.expected_sources
        )
        term_match = sum(
            1 for term in benchmark.expected_terms
            if term.lower() in text
        ) >= len(benchmark.expected_terms) // 2  # At least half the terms
        relevant_mask.append(source_match or term_match)

    # Precision@k: fraction of retrieved that are relevant
    relevant_count = sum(relevant_mask)
    precision_at_k = relevant_count / top_k if top_k > 0 else 0.0

    # Recall: did we find ANY of the expected sources? (sources are alternatives, not requirements)
    found_sources = []
    for source in sources:
        for exp in benchmark.expected_sources:
            if exp.lower() in source.lower():
                found_sources.append(source)
                break
    # Recall is 1.0 if we found at least one expected source, 0.0 otherwise
    recall = 1.0 if found_sources else 0.0

    # Term coverage: fraction of expected terms found
    found_terms = [
        term for term in benchmark.expected_terms
        if term.lower() in combined_text
    ]
    missing_terms = [
        term for term in benchmark.expected_terms
        if term.lower() not in combined_text
    ]
    term_coverage = len(found_terms) / len(benchmark.expected_terms) if benchmark.expected_terms else 1.0

    # MRR: reciprocal rank of first relevant result
    mrr = 0.0
    for i, is_relevant in enumerate(relevant_mask):
        if is_relevant:
            mrr = 1.0 / (i + 1)
            break

    return QueryResult(
        query=benchmark.query,
        description=benchmark.description,
        precision_at_k=precision_at_k,
        recall=recall,
        term_coverage=term_coverage,
        mrr=mrr,
        top_k=top_k,
        relevant_count=relevant_count,
        expected_sources=benchmark.expected_sources,
        found_sources=list(set(found_sources)),
        missing_terms=missing_terms,
    )


# =============================================================================
# Benchmark Runner
# =============================================================================

def run_benchmarks(
    config: dict,
    queries: list[BenchmarkQuery] | None = None,
    top_k: int = 5,
    verbose: bool = False,
) -> BenchmarkReport:
    """Run benchmark suite against current vectorstore backend.

    Args:
        config: Application config dict
        queries: Optional subset of queries to run (defaults to all)
        top_k: Number of results to retrieve per query
        verbose: Print progress during run

    Returns:
        BenchmarkReport with aggregate metrics
    """
    from llama_index.core import Settings, VectorStoreIndex

    from librarian.config import DEFAULT_EMBED_MODEL, DEFAULT_VECTOR_BACKEND
    from librarian.embeddings import get_embed_model

    queries = queries or BENCHMARK_QUERIES
    vs_config = config.get("vector_store", {})
    backend = vs_config.get("backend", DEFAULT_VECTOR_BACKEND)
    collection_names = get_collection_names(config)

    if verbose:
        print(f"Running benchmarks against backend: {backend}")
        print(f"Collections: {collection_names}")

    # Set up embedding model (CPU for consistent benchmarking)
    embed_config = dict(config.get("embedding", {}))
    embed_config.setdefault("model", DEFAULT_EMBED_MODEL)
    embed_config["device"] = "cpu"

    if verbose:
        print(f"Loading embedding model: {embed_config['model']}")

    Settings.embed_model = get_embed_model({"embedding": embed_config})

    # Get vectorstore
    store = get_vector_store(config)

    results = []
    for i, benchmark in enumerate(queries):
        if verbose:
            print(f"[{i+1}/{len(queries)}] {benchmark.query[:50]}...")

        # Select collection
        if benchmark.collection == "equations":
            collection = collection_names["equations"]
        elif benchmark.collection == "chapters":
            collection = collection_names["chapters"]
        else:
            collection = collection_names["full"]

        # Check if collection exists
        if not store.collection_exists(collection):
            if verbose:
                print(f"  Skipping - collection '{collection}' does not exist")
            continue

        # Create retriever for this collection
        llama_store = store.get_llama_store(collection)
        index = VectorStoreIndex.from_vector_store(llama_store)
        retriever = index.as_retriever(similarity_top_k=top_k)

        # Retrieve and score
        nodes = retriever.retrieve(benchmark.query)
        result = score_result(benchmark, nodes, top_k)
        results.append(result)

        if verbose:
            status = "PASS" if result.recall > 0.5 and result.term_coverage > 0.5 else "FAIL"
            print(f"  {status}: P@{top_k}={result.precision_at_k:.2f} "
                  f"R={result.recall:.2f} TC={result.term_coverage:.2f} "
                  f"MRR={result.mrr:.2f}")

    return BenchmarkReport(
        backend=backend,
        collection_names=collection_names,
        total_queries=len(results),
        results=results,
    )


def print_report(report: BenchmarkReport):
    """Print a formatted benchmark report."""
    print("\n" + "=" * 70)
    print(f"BENCHMARK REPORT: {report.backend}")
    print("=" * 70)
    print(f"Total queries: {report.total_queries}")
    print(f"Mean Precision@k: {report.mean_precision:.3f}")
    print(f"Mean Recall:      {report.mean_recall:.3f}")
    print(f"Mean Term Cov.:   {report.mean_term_coverage:.3f}")
    print(f"Mean MRR:         {report.mean_mrr:.3f}")

    # Show failures
    failures = [r for r in report.results if r.recall < 0.5 or r.term_coverage < 0.5]
    if failures:
        print(f"\n--- Failures ({len(failures)}) ---")
        for r in failures:
            print(f"\n  Query: {r.query}")
            print(f"  Description: {r.description}")
            print(f"  Recall: {r.recall:.2f}, Term Coverage: {r.term_coverage:.2f}")
            if r.missing_terms:
                print(f"  Missing terms: {r.missing_terms}")
            print(f"  Found sources: {r.found_sources or 'None'}")

    print("\n" + "=" * 70)


def compare_backends(
    config: dict,
    backends: list[str] | None = None,
    top_k: int = 5,
    verbose: bool = False,
) -> dict[str, BenchmarkReport]:
    """Run benchmarks against multiple backends and compare.

    Args:
        config: Base config (backend will be overridden)
        backends: List of backends to test (defaults to all available)
        top_k: Number of results per query
        verbose: Print progress

    Returns:
        Dict mapping backend name to BenchmarkReport
    """
    if backends is None:
        backends = ["qdrant-file", "pgvector"]

    reports = {}
    for backend in backends:
        test_config = config.copy()
        test_config["vector_store"] = config.get("vector_store", {}).copy()
        test_config["vector_store"]["backend"] = backend

        try:
            report = run_benchmarks(test_config, top_k=top_k, verbose=verbose)
            reports[backend] = report
        except Exception as e:
            if verbose:
                print(f"Skipping {backend}: {e}")

    return reports


def print_comparison(reports: dict[str, BenchmarkReport]):
    """Print comparison table of multiple backends."""
    print("\n" + "=" * 70)
    print("BACKEND COMPARISON")
    print("=" * 70)
    print(f"{'Backend':<15} {'Precision':<12} {'Recall':<12} {'Term Cov':<12} {'MRR':<12}")
    print("-" * 70)

    for backend, report in sorted(reports.items()):
        print(f"{backend:<15} {report.mean_precision:<12.3f} {report.mean_recall:<12.3f} "
              f"{report.mean_term_coverage:<12.3f} {report.mean_mrr:<12.3f}")

    print("=" * 70)


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    """CLI entry point for running benchmarks."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run semantic search benchmarks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  librarian-benchmark                        Run with current backend
  librarian-benchmark --backend qdrant-file  Run with specific backend
  librarian-benchmark --compare              Compare all available backends
  librarian-benchmark --json                 Output results as JSON
  librarian-benchmark -k 10                  Use top-10 instead of top-5
        """,
    )
    parser.add_argument(
        "--backend",
        choices=["qdrant-file", "pgvector"],
        help="Override backend from config",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare all available backends",
    )
    parser.add_argument(
        "-k", "--top-k",
        type=int,
        default=5,
        help="Number of results to retrieve (default: 5)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show progress during benchmark",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()
    config = load_config()

    if args.backend:
        config["vector_store"]["backend"] = args.backend

    if args.compare:
        reports = compare_backends(config, top_k=args.top_k, verbose=args.verbose)
        if args.json:
            print(json.dumps({k: v.to_dict() for k, v in reports.items()}, indent=2))
        else:
            print_comparison(reports)
    else:
        report = run_benchmarks(config, top_k=args.top_k, verbose=args.verbose)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print_report(report)

    # Exit with error if mean recall < 0.5
    if not args.compare:
        sys.exit(0 if report.mean_recall >= 0.5 else 1)


if __name__ == "__main__":
    main()
