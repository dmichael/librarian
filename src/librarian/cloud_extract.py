"""Cloud extraction using Modal for parallel GPU processing.

This module offloads PDF extraction to Modal's cloud GPUs, enabling:
- Parallel extraction of multiple books simultaneously
- 10x+ speedup on A100 vs local Apple Silicon
- No local GPU contention

Usage:
    librarian-extract --cloud              # Extract all pending books on cloud
    librarian-extract --cloud --book-id 5  # Extract specific book on cloud

Setup:
    pip install modal
    modal setup  # One-time authentication
"""

import json
import sys
from pathlib import Path

# Modal import is optional - only needed when --cloud is used
try:
    import modal
    MODAL_AVAILABLE = True
except ImportError:
    MODAL_AVAILABLE = False


def check_modal_available():
    """Check if Modal is installed and configured."""
    if not MODAL_AVAILABLE:
        print("Modal not installed. Run: pip install modal", file=sys.stderr)
        print("Then authenticate: modal setup", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# Modal App Definition
# ---------------------------------------------------------------------------

if MODAL_AVAILABLE:
    app = modal.App("librarian-extract")

    # GPU image with marker pre-installed and models pre-downloaded
    # Using debian-slim with Python 3.12, adding system deps for marker
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install(
            "poppler-utils",  # PDF utilities
            "libgl1",         # OpenGL for image processing
            "libglib2.0-0",   # GLib
            # weasyprint system deps (for EPUB → PDF conversion)
            "libpango-1.0-0",
            "libpangoft2-1.0-0",
            "libharfbuzz0b",
            "libffi-dev",
        )
        .pip_install(
            "marker-pdf>=1.0.0",
            "markdownify>=0.11.0",
            "weasyprint>=60.0",  # Required for EPUB support (marker epub→pdf)
            "ebooklib>=0.18",   # Required for EPUB support (marker epub loading)
        )
        .pip_install("reportlab")
        .run_commands(
            # Pre-download marker/surya models (~3GB) by running on a tiny test PDF
            "python -c \""
            "from reportlab.lib.pagesizes import letter; "
            "from reportlab.pdfgen import canvas; "
            "c = canvas.Canvas('/tmp/test.pdf', pagesize=letter); "
            "c.drawString(72, 720, 'test'); c.save()\"",
            "marker_single /tmp/test.pdf --output_dir /tmp/marker_out --output_format chunks || true",
            "rm -rf /tmp/test.pdf /tmp/marker_out",
        )
    )

    @app.function(
        gpu="A100",
        image=image,
        timeout=7200,  # 2 hour max per book
        retries=1,
    )
    def extract_pdf_remote(pdf_bytes: bytes, book_id: int, filename: str) -> dict:
        """Run marker extraction on cloud GPU.

        Args:
            pdf_bytes: Raw PDF file content
            book_id: Calibre book ID
            filename: Original filename for logging

        Returns:
            Dict with 'chunks_json', 'meta_json', 'markdown', 'success', 'error'
        """
        import tempfile
        import subprocess
        import os

        result = {
            "book_id": book_id,
            "filename": filename,
            "success": False,
            "chunks_json": None,
            "meta_json": None,
            "markdown": None,
            "error": None,
        }

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Preserve original extension so marker detects format correctly
                suffix = Path(filename).suffix or ".pdf"
                input_path = Path(tmpdir) / f"input{suffix}"
                output_dir = Path(tmpdir) / "output"
                output_dir.mkdir()

                input_path.write_bytes(pdf_bytes)

                # Run marker_single - capture stderr for error reporting
                cmd = [
                    "marker_single",
                    str(input_path),
                    "--output_dir", str(output_dir),
                    "--output_format", "chunks",
                ]

                print(f"[{book_id}] Starting marker extraction: {filename}", flush=True)
                # Stream stdout/stderr live so Modal logs show progress
                proc = subprocess.run(cmd, text=True, stderr=subprocess.PIPE)
                print(f"[{book_id}] Marker finished with code: {proc.returncode}", flush=True)

                if proc.returncode != 0:
                    stderr = proc.stderr[:500] if proc.stderr else ""
                    result["error"] = f"marker_single failed with code {proc.returncode}: {stderr}"
                    return result

                # Find output files (marker creates subdirectory with variable name)
                chunks_file = None
                meta_file = None

                for json_file in output_dir.rglob("*.json"):
                    if json_file.name.endswith("_meta.json"):
                        meta_file = json_file
                    else:
                        chunks_file = json_file

                if not chunks_file:
                    result["error"] = "No chunks output found"
                    return result

                # Read output files
                result["chunks_json"] = chunks_file.read_text()

                if meta_file:
                    result["meta_json"] = meta_file.read_text()

                # Generate markdown from chunks
                result["markdown"] = _chunks_to_markdown_cloud(result["chunks_json"])
                result["success"] = True

        except Exception as e:
            result["error"] = str(e)

        return result


def _chunks_to_markdown_cloud(chunks_json_str: str) -> str:
    """Convert marker chunks JSON to markdown (runs on cloud)."""
    import markdownify

    data = json.loads(chunks_json_str)
    lines = []

    chunks = data if isinstance(data, list) else data.get("chunks", data.get("blocks", []))

    for chunk in chunks:
        if isinstance(chunk, str):
            lines.append(chunk)
        elif isinstance(chunk, dict):
            if "html" in chunk:
                md = markdownify.markdownify(chunk["html"], heading_style="ATX")
                lines.append(md.strip())
            elif "text" in chunk:
                lines.append(chunk["text"])
            elif "content" in chunk:
                lines.append(chunk["content"])

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Local Interface
# ---------------------------------------------------------------------------

def extract_books_cloud(
    books: list[dict],
    library_path: Path,
    output_path: Path,
    dry_run: bool = False,
    max_parallel: int = 0,
) -> tuple[int, int]:
    """Extract multiple books in parallel using Modal.

    Args:
        books: List of Calibre book dicts with 'id', 'title', 'formats'
        library_path: Path to Calibre library
        output_path: Base output directory for extracted content
        dry_run: If True, just print what would be done
        max_parallel: Max concurrent extractions (0 = unlimited)

    Returns:
        Tuple of (succeeded_count, failed_count)
    """
    if not check_modal_available():
        return 0, len(books)

    from librarian.extract import get_source_file, update_calibre_extraction_state, compute_file_hash

    # Collect books with their PDF paths
    jobs = []
    for book in books:
        book_id = book["id"]
        title = book.get("title", "Unknown")

        source_file, needs_conversion = get_source_file(book)

        if not source_file or not source_file.exists():
            print(f"[{book_id}] {title}: No source file, skipping")
            continue

        if needs_conversion:
            print(f"[{book_id}] {title}: Kindle format needs local conversion first, skipping")
            continue

        if source_file.suffix.lower() != ".pdf":
            print(f"[{book_id}] {title}: Not a PDF ({source_file.suffix}), skipping cloud extraction")
            continue

        jobs.append({
            "book": book,
            "source_file": source_file,
            "title": title,
        })

    if not jobs:
        print("No PDF books to extract")
        return 0, 0

    if dry_run:
        print(f"\nWould extract {len(jobs)} books on cloud (Modal A100):")
        for job in jobs:
            print(f"  [{job['book']['id']}] {job['title']}")
        if max_parallel > 0:
            print(f"\nMax parallelism: {max_parallel}")
        return len(jobs), 0

    parallel_note = f" (max {max_parallel} concurrent)" if max_parallel > 0 else ""
    print(f"\nLaunching {len(jobs)} extractions on Modal{parallel_note}...")

    # Run within Modal app context
    with app.run():
        if max_parallel > 0:
            # Batched execution with concurrency limit
            return _extract_with_limit(jobs, library_path, output_path, max_parallel)

        # Unlimited parallelism - launch all at once
        futures = []
        for job in jobs:
            book = job["book"]
            pdf_bytes = job["source_file"].read_bytes()
            future = extract_pdf_remote.spawn(
                pdf_bytes,
                book["id"],
                job["source_file"].name,
            )
            futures.append((job, future))
            print(f"  [{book['id']}] {job['title']}: Launched")

        # Collect results as they complete
        succeeded = 0
        failed = 0

        for job, future in futures:
            book = job["book"]
            book_id = book["id"]
            title = job["title"]

            try:
                result = future.get()

                if result["success"]:
                    # Save results locally
                    book_output = output_path / str(book_id)
                    book_output.mkdir(parents=True, exist_ok=True)

                    # Write chunks JSON
                    (book_output / f"{book_id}.json").write_text(result["chunks_json"])

                    # Write meta JSON if present
                    if result["meta_json"]:
                        (book_output / f"{book_id}_meta.json").write_text(result["meta_json"])

                    # Write markdown
                    (book_output / f"{book_id}.md").write_text(result["markdown"])

                    # Update Calibre state
                    source_hash = compute_file_hash(job["source_file"])
                    update_calibre_extraction_state(library_path, book_id, source_hash)

                    print(f"  [{book_id}] {title}: Done")
                    succeeded += 1
                else:
                    print(f"  [{book_id}] {title}: Failed - {result['error']}", file=sys.stderr)
                    failed += 1

            except Exception as e:
                print(f"  [{book_id}] {title}: Exception - {e}", file=sys.stderr)
                failed += 1

    print(f"\nCloud extraction complete: {succeeded} succeeded, {failed} failed")
    return succeeded, failed


def _extract_with_limit(
    jobs: list[dict],
    library_path: Path,
    output_path: Path,
    max_parallel: int,
) -> tuple[int, int]:
    """Extract with concurrency limit using sliding window."""
    from librarian.extract import update_calibre_extraction_state, compute_file_hash

    succeeded = 0
    failed = 0
    active = []  # List of (job, future) tuples

    job_queue = list(jobs)  # Copy to avoid mutation

    def launch_one():
        """Launch the next job from queue."""
        if not job_queue:
            return
        job = job_queue.pop(0)
        book = job["book"]
        pdf_bytes = job["source_file"].read_bytes()
        future = extract_pdf_remote.spawn(
            pdf_bytes,
            book["id"],
            job["source_file"].name,
        )
        active.append((job, future))
        print(f"  [{book['id']}] {job['title']}: Launched")

    def collect_one():
        """Wait for any job to complete, process result."""
        nonlocal succeeded, failed
        if not active:
            return

        # Poll for completed futures
        for i, (job, future) in enumerate(active):
            try:
                # Try non-blocking get (Modal futures support this)
                result = future.get(timeout=0.1)

                # Got result - process it
                active.pop(i)
                book = job["book"]
                book_id = book["id"]
                title = job["title"]

                if result["success"]:
                    book_output = output_path / str(book_id)
                    book_output.mkdir(parents=True, exist_ok=True)
                    (book_output / f"{book_id}.json").write_text(result["chunks_json"])
                    if result["meta_json"]:
                        (book_output / f"{book_id}_meta.json").write_text(result["meta_json"])
                    (book_output / f"{book_id}.md").write_text(result["markdown"])
                    source_hash = compute_file_hash(job["source_file"])
                    update_calibre_extraction_state(library_path, book_id, source_hash)
                    print(f"  [{book_id}] {title}: Done")
                    succeeded += 1
                else:
                    print(f"  [{book_id}] {title}: Failed - {result['error']}", file=sys.stderr)
                    failed += 1
                return

            except TimeoutError:
                continue
            except Exception as e:
                active.pop(i)
                book_id = job["book"]["id"]
                print(f"  [{book_id}] {job['title']}: Exception - {e}", file=sys.stderr)
                failed += 1
                return

        # No completed futures, wait a bit
        import time
        time.sleep(1)

    # Initial launch up to max_parallel
    for _ in range(min(max_parallel, len(job_queue))):
        launch_one()

    # Process until all done
    while active or job_queue:
        collect_one()
        # Refill to max_parallel
        while len(active) < max_parallel and job_queue:
            launch_one()

    return succeeded, failed


def main():
    """Standalone CLI for cloud extraction."""
    from librarian.config import expand_path, load_config
    from librarian.extract import get_books_for_extraction, get_calibre_books

    if not check_modal_available():
        sys.exit(1)

    # Parse args
    dry_run = "--dry-run" in sys.argv
    book_ids = []
    max_parallel = 0  # 0 = unlimited

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--book-id" and i + 1 < len(sys.argv):
            book_ids.append(int(sys.argv[i + 1]))
            i += 1
        elif sys.argv[i] in ("--parallel", "-p") and i + 1 < len(sys.argv):
            max_parallel = int(sys.argv[i + 1])
            i += 1
        elif sys.argv[i] in ("--help", "-h"):
            print("Cloud extraction using Modal A100 GPUs")
            print("\nUsage: librarian-extract-cloud [OPTIONS]")
            print("\nOptions:")
            print("  --dry-run        Show what would be extracted")
            print("  --book-id N      Extract specific book ID (can repeat)")
            print("  --parallel N     Max concurrent extractions (default: unlimited)")
            print("  --help, -h       Show this help")
            sys.exit(0)
        i += 1

    config = load_config()
    library_path = expand_path(config["library_path"])
    output_path = expand_path(config["output_path"])

    # Get books to extract
    if book_ids:
        books = [b for b in get_calibre_books(library_path) if b["id"] in book_ids]
    else:
        books = get_books_for_extraction(library_path)

    if not books:
        print("No books need extraction")
        return

    succeeded, failed = extract_books_cloud(
        books, library_path, output_path, dry_run, max_parallel
    )
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
