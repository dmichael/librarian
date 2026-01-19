"""Extract content from Calibre library to markdown."""

import fcntl
import hashlib
import os
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import markdownify

from librarian.config import expand_path, load_config
from librarian.files import find_content_json
from librarian import calibre

TOOL_VERSION = "marker-0.1.0"  # Update when extraction tools change


def get_calibre_books(library_path: Path, max_retries: int = 3) -> list[dict]:
    """Query Calibre for all books with their metadata.

    Retries with exponential backoff if Calibre database is locked.
    """
    import json

    cmd = [
        "calibredb", "list",
        "--library-path", str(library_path),
        "--fields", "id,title,formats,*source_hash,*status",
        "--for-machine",
    ]

    for attempt in range(max_retries):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)

        # Check if it's a lock contention error (retryable)
        if "Another calibre program" in result.stderr:
            delay = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
            print(f"Calibre busy, retrying in {delay}s...", file=sys.stderr)
            time.sleep(delay)
            continue

        # Non-retryable error
        print(f"Error querying Calibre: {result.stderr}", file=sys.stderr)
        return []

    print(f"Calibre unavailable after {max_retries} retries", file=sys.stderr)
    return []


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return f"sha256:{sha256.hexdigest()}"


# Formats we can extract directly
DIRECT_FORMATS = [".epub", ".pdf"]
# Formats that need conversion to EPUB first
KINDLE_FORMATS = [".azw3", ".azw", ".mobi", ".kfx"]

# Lock for serializing marker_single calls (surya crashes with concurrent MPS access)
MARKER_LOCK = Path("/tmp/librarian-marker.lock")
LOCK_STALE_SECONDS = 3600  # Consider lock stale after 1 hour


def _is_lock_stale(lock_path: Path) -> bool:
    """Check if lock file is stale (holder crashed)."""
    if not lock_path.exists():
        return False
    try:
        mtime = lock_path.stat().st_mtime
        if time.time() - mtime > LOCK_STALE_SECONDS:
            return True
        # Try non-blocking lock to see if it's held
        with open(lock_path, "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return True  # Lock was free = previous holder crashed
            except BlockingIOError:
                return False  # Lock is actively held
    except (OSError, IOError):
        return True  # Can't check = assume stale


def _clear_stale_lock(lock_path: Path):
    """Remove stale lock file."""
    try:
        lock_path.unlink()
        print(f"Cleared stale lock: {lock_path}")
    except FileNotFoundError:
        pass


def get_source_file(book: dict) -> tuple[Path | None, bool]:
    """Get the source file path from Calibre book record.

    Returns:
        Tuple of (path, needs_conversion) where needs_conversion is True
        if the format requires conversion to EPUB before extraction.
    """
    formats = book.get("formats", [])
    if not formats:
        return None, False

    # Prefer formats we can extract directly
    for fmt in formats:
        if fmt.lower().endswith(".epub"):
            return Path(fmt), False
    for fmt in formats:
        if fmt.lower().endswith(".pdf"):
            return Path(fmt), False

    # Check for Kindle formats that need conversion
    for fmt in formats:
        suffix = Path(fmt).suffix.lower()
        if suffix in KINDLE_FORMATS:
            return Path(fmt), True

    # Fall back to first format (may not be extractable)
    return Path(formats[0]), False


def convert_to_epub(source: Path, output_dir: Path, book_id: int | None = None) -> Path | None:
    """Convert Kindle format to EPUB using Calibre's ebook-convert.

    Args:
        source: Path to source file
        output_dir: Directory for output (should be output_path/{book_id}/)
        book_id: Optional book ID for naming; if None, uses directory name

    Returns:
        Path to converted EPUB, or None if conversion failed.
    """
    # Use book_id for naming, fallback to directory name (which is book_id)
    name = str(book_id) if book_id else output_dir.name
    epub_path = output_dir / f"{name}.epub"

    cmd = [
        "ebook-convert",
        str(source),
        str(epub_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ebook-convert failed: {result.stderr}", file=sys.stderr)
        return None

    if not epub_path.exists():
        print("ebook-convert produced no output", file=sys.stderr)
        return None

    return epub_path


def needs_extraction(book: dict, source_file: Path, output_dir: Path) -> bool:
    """Check if book needs extraction."""
    book_id = book["id"]
    book_dir = output_dir / str(book_id)

    if find_content_json(book_dir) is None:
        return True

    stored_hash = book.get("*source_hash")
    if not stored_hash:
        return True

    return compute_file_hash(source_file) != stored_hash


def find_marker_single() -> str:
    """Find marker_single executable, checking venv first."""
    import shutil

    # Check if marker_single is in PATH
    marker = shutil.which("marker_single")
    if marker:
        return marker

    # Check in the same venv as this script
    venv_bin = Path(sys.executable).parent
    marker_in_venv = venv_bin / "marker_single"
    if marker_in_venv.exists():
        return str(marker_in_venv)

    return "marker_single"  # Fall back to PATH lookup


def _run_marker(source: Path, output_dir: Path, output_format: str) -> bool:
    """Run marker_single with specified output format (serialized via lock).

    Args:
        source: Path to PDF file
        output_dir: Directory for output
        output_format: 'markdown' or 'json'

    Returns:
        True if successful, False otherwise
    """
    marker_cmd = find_marker_single()
    cmd = [
        marker_cmd,
        str(source),
        "--output_dir", str(output_dir),
        "--output_format", output_format,
    ]

    # Clear stale lock if previous process crashed
    if _is_lock_stale(MARKER_LOCK):
        _clear_stale_lock(MARKER_LOCK)

    # Serialize marker_single - surya crashes with concurrent MPS access
    with open(MARKER_LOCK, "w") as lock:
        lock.write(f"{os.getpid()}\n")
        lock.flush()

        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            # Run marker directly - let it inherit stdout/stderr for tqdm progress bars
            result = subprocess.run(cmd)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

    return result.returncode == 0


def _chunks_to_markdown(chunks_path: Path) -> str:
    """Convert marker chunks output to markdown.

    The chunks format is a flat list of blocks, each with 'html' content.
    We convert HTML to markdown using markdownify.

    Args:
        chunks_path: Path to marker chunks JSON output

    Returns:
        Markdown string
    """
    import json

    with open(chunks_path) as f:
        data = json.load(f)

    lines = []

    # Chunks format is a flat list of blocks (key may be "chunks" or "blocks")
    chunks = data if isinstance(data, list) else data.get("chunks", data.get("blocks", []))

    for chunk in chunks:
        if isinstance(chunk, str):
            lines.append(chunk)
        elif isinstance(chunk, dict):
            # Each chunk has 'html' with the complete rendered content
            if "html" in chunk:
                md = markdownify.markdownify(chunk["html"], heading_style="ATX")
                lines.append(md.strip())
            elif "text" in chunk:
                lines.append(chunk["text"])
            elif "content" in chunk:
                lines.append(chunk["content"])

    return "\n\n".join(lines)


def _collect_marker_output(output_dir: Path) -> bool:
    """Collect marker output and rename to predictable names.

    Marker creates a subdirectory with variable names based on the PDF filename.
    This function moves the files to predictable names:
    - {book_id}.json: Content blocks
    - {book_id}_meta.json: Document metadata

    Args:
        output_dir: Book output directory (e.g., converted/153/)

    Returns:
        True if content file was found and moved, False otherwise
    """
    book_id = output_dir.name  # Directory name is the book ID

    # Find marker's content JSON (not ending in _meta.json)
    content_file = None
    meta_file = None
    marker_subdir = None

    for json_file in output_dir.rglob("*.json"):
        if json_file.parent == output_dir:
            continue  # Skip files already at top level
        marker_subdir = json_file.parent
        if json_file.name.endswith("_meta.json"):
            meta_file = json_file
        else:
            content_file = json_file

    if not content_file:
        return False

    # Move to predictable names
    content_file.rename(output_dir / f"{book_id}.json")
    if meta_file:
        meta_file.rename(output_dir / f"{book_id}_meta.json")

    # Clean up empty marker subdirectory
    if marker_subdir and marker_subdir.exists():
        try:
            marker_subdir.rmdir()
        except OSError:
            pass  # Not empty, leave it

    return True


def extract_pdf(source: Path, output_dir: Path) -> bool:
    """Extract PDF using marker chunks format.

    Produces:
    - {book_id}.json: Content blocks
    - {book_id}_meta.json: Document metadata
    - {book_id}.md: Markdown for human reading
    """
    try:
        print("  Extracting with marker (chunks format)...", flush=True)
        if not _run_marker(source, output_dir, "chunks"):
            print("marker_single failed", file=sys.stderr)
            return False

        if not _collect_marker_output(output_dir):
            print("  Error: No content JSON found", file=sys.stderr)
            return False

        book_id = output_dir.name
        content_file = output_dir / f"{book_id}.json"

        # Generate markdown for human reading
        markdown = _chunks_to_markdown(content_file)
        (output_dir / f"{book_id}.md").write_text(markdown)

        return True

    except FileNotFoundError:
        print("marker_single not found. Install with: pip install marker-pdf", file=sys.stderr)
        return False


def extract_epub(source: Path, output_dir: Path) -> bool:
    """Extract EPUB to markdown by parsing XHTML content."""
    book_id = output_dir.name
    output_file = output_dir / f"{book_id}.md"
    chapters_dir = output_dir / "chapters"
    chapters_dir.mkdir(exist_ok=True)

    try:
        with zipfile.ZipFile(source, 'r') as epub:
            # Find the OPF file (contains spine/reading order)
            container = epub.read('META-INF/container.xml')
            container_tree = ET.fromstring(container)
            ns = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
            opf_path = container_tree.find('.//c:rootfile', ns).get('full-path')
            opf_dir = str(Path(opf_path).parent)
            if opf_dir == '.':
                opf_dir = ''

            # Parse OPF for manifest and spine
            opf_content = epub.read(opf_path)
            opf_tree = ET.fromstring(opf_content)
            opf_ns = {'opf': 'http://www.idpf.org/2007/opf'}

            # Build manifest lookup (id -> href)
            manifest = {}
            for item in opf_tree.findall('.//{http://www.idpf.org/2007/opf}item'):
                item_id = item.get('id')
                href = item.get('href')
                media_type = item.get('media-type', '')
                if 'html' in media_type or 'xhtml' in media_type:
                    manifest[item_id] = href

            # Get spine order
            spine_items = []
            for itemref in opf_tree.findall('.//{http://www.idpf.org/2007/opf}itemref'):
                idref = itemref.get('idref')
                if idref in manifest:
                    spine_items.append(manifest[idref])

            # Extract content in spine order
            full_content = []
            for i, href in enumerate(spine_items):
                # Resolve path relative to OPF
                if opf_dir:
                    content_path = f"{opf_dir}/{href}"
                else:
                    content_path = href

                try:
                    html_content = epub.read(content_path).decode('utf-8')
                    # Convert HTML to Markdown
                    md_content = markdownify.markdownify(html_content, heading_style="ATX")
                    # Clean up XML declarations and excessive whitespace
                    lines = []
                    for line in md_content.split('\n'):
                        # Skip XML declarations
                        if line.strip().startswith('xml version='):
                            continue
                        if line.strip() or lines:
                            lines.append(line)
                    md_content = '\n'.join(lines).strip()

                    full_content.append(md_content)

                    # Save individual chapter
                    chapter_file = chapters_dir / f"{i:03d}.md"
                    chapter_file.write_text(md_content)
                except KeyError:
                    continue  # Skip missing files

            # Write full content
            output_file.write_text('\n\n---\n\n'.join(full_content))
            return True

    except (zipfile.BadZipFile, ET.ParseError, KeyError) as e:
        print(f"EPUB extraction failed: {e}", file=sys.stderr)
        return False


def extract_book(book: dict, source_file: Path, output_dir: Path, needs_conversion: bool = False) -> bool:
    """Extract a single book to markdown.

    Args:
        book: Calibre book metadata dict
        source_file: Path to the source file
        output_dir: Base output directory for extracted content
        needs_conversion: If True, convert Kindle format to EPUB first
    """
    book_id = book["id"]
    book_output = output_dir / str(book_id)
    book_output.mkdir(parents=True, exist_ok=True)

    suffix = source_file.suffix.lower()

    # Handle Kindle formats by converting to EPUB first
    if needs_conversion or suffix in KINDLE_FORMATS:
        print(f"  Converting {suffix} to EPUB...", flush=True)
        epub_path = convert_to_epub(source_file, book_output, book_id)
        if epub_path is None:
            return False
        source_file = epub_path
        suffix = ".epub"

    if suffix == ".pdf":
        return extract_pdf(source_file, book_output)
    elif suffix == ".epub":
        return extract_epub(source_file, book_output)
    else:
        print(f"Unsupported format: {suffix}", file=sys.stderr)
        return False


def update_calibre_extraction_state(library_path: Path, book_id: int, source_hash: str):
    """Update Calibre custom columns after extraction."""
    now = datetime.now().isoformat()

    cmds = [
        ["calibredb", "set_custom", "--library-path", str(library_path),
         "source_hash", str(book_id), source_hash],
        ["calibredb", "set_custom", "--library-path", str(library_path),
         "extraction_date", str(book_id), now],
        ["calibredb", "set_custom", "--library-path", str(library_path),
         "extraction_tool", str(book_id), TOOL_VERSION],
    ]

    for cmd in cmds:
        subprocess.run(cmd, capture_output=True)

    # Update pipeline status
    calibre.set_status(book_id, "extracted", library_path)


def parse_args():
    """Parse command line arguments."""
    args = {
        "dry_run": False,
        "force": False,
        "book_ids": [],
    }

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--force":
            args["force"] = True
        elif arg == "--book-id" and i + 1 < len(sys.argv):
            args["book_ids"].append(int(sys.argv[i + 1]))
            i += 1
        i += 1

    return args


def get_books_for_extraction(library_path: Path, force: bool = False) -> list[dict]:
    """Get books that need extraction based on Calibre *status.

    Returns books with status='imported' (or no status for legacy books).
    With force=True, returns all books regardless of status.
    """
    books = get_calibre_books(library_path)
    if force:
        return books

    # Filter to books needing extraction
    return [b for b in books if b.get("*status") in (None, "imported")]


def main():
    """CLI entry point for extraction.

    Extracts books with *status='imported' to markdown.
    Updates *status to 'extracted' on success.
    """
    args = parse_args()

    config = load_config()
    library_path = expand_path(config["library_path"])
    output_path = expand_path(config["output_path"])

    output_path.mkdir(parents=True, exist_ok=True)

    # Get books needing extraction (uses Calibre *status)
    if args["book_ids"]:
        # Specific book IDs requested - get all and filter
        books = [b for b in get_calibre_books(library_path) if b["id"] in args["book_ids"]]
    else:
        books = get_books_for_extraction(library_path, force=args["force"])

    if not books:
        print("No books need extraction")
        return

    print(f"Found {len(books)} books to extract")

    succeeded = 0
    failed = 0

    for book in books:
        book_id = book["id"]
        title = book.get("title", "Unknown")

        source_file, needs_conversion = get_source_file(book)
        if not source_file or not source_file.exists():
            print(f"[{book_id}] {title}: No source file found, skipping", flush=True)
            continue

        # Check if already extracted (file exists) unless forcing
        if not args["force"] and not needs_extraction(book, source_file, output_path):
            print(f"[{book_id}] {title}: Already extracted, skipping", flush=True)
            continue

        if args["dry_run"]:
            fmt_note = f" (convert to EPUB)" if needs_conversion else ""
            print(f"[{book_id}] {title}: Would extract from {source_file.suffix}{fmt_note}", flush=True)
            continue

        print(f"[{book_id}] {title}: Extracting...", flush=True)

        if extract_book(book, source_file, output_path, needs_conversion):
            source_hash = compute_file_hash(source_file)
            update_calibre_extraction_state(library_path, book_id, source_hash)
            print(f"[{book_id}] {title}: Done", flush=True)
            succeeded += 1
        else:
            print(f"[{book_id}] {title}: Extraction failed", file=sys.stderr)
            failed += 1

    if not args["dry_run"]:
        print(f"\nExtraction complete: {succeeded} succeeded, {failed} failed")


if __name__ == "__main__":
    main()
