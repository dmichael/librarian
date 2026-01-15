"""Extract content from Calibre library to markdown."""

import hashlib
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import markdownify

from librarian.config import expand_path, load_config

TOOL_VERSION = "marker-0.1.0"  # Update when extraction tools change


def get_calibre_books(library_path: Path) -> list[dict]:
    """Query Calibre for all books with their metadata."""
    cmd = [
        "calibredb", "list",
        "--library-path", str(library_path),
        "--fields", "id,title,formats,*source_hash",
        "--for-machine",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error querying Calibre: {result.stderr}", file=sys.stderr)
        return []

    import json
    return json.loads(result.stdout)


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return f"sha256:{sha256.hexdigest()}"


def get_source_file(book: dict) -> Path | None:
    """Get the source file path from Calibre book record."""
    formats = book.get("formats", [])
    if not formats:
        return None
    # Prefer EPUB, then PDF
    for fmt in formats:
        if fmt.lower().endswith(".epub"):
            return Path(fmt)
    for fmt in formats:
        if fmt.lower().endswith(".pdf"):
            return Path(fmt)
    # Fall back to first format
    return Path(formats[0]) if formats else None


def needs_extraction(book: dict, source_file: Path, output_dir: Path) -> bool:
    """Check if book needs extraction."""
    book_id = book["id"]
    stored_hash = book.get("*source_hash")

    # No hash stored = never extracted
    if not stored_hash:
        return True

    # Output doesn't exist
    if not (output_dir / str(book_id) / "full.md").exists():
        return True

    # Hash mismatch = source changed
    current_hash = compute_file_hash(source_file)
    if current_hash != stored_hash:
        return True

    return False


def extract_pdf(source: Path, output_dir: Path) -> bool:
    """Extract PDF to markdown using Marker."""
    try:
        cmd = [
            "marker_single",
            str(source),
            "--output_dir", str(output_dir),
            "--output_format", "markdown",
        ]
        # Stream output instead of capturing - marker shows progress on stderr
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"marker_single failed with exit code {result.returncode}", file=sys.stderr)
            return False
        # Marker creates a subdirectory with the PDF name - find and move the .md file
        for md_file in output_dir.rglob("*.md"):
            md_file.rename(output_dir / "full.md")
            break
        # Clean up subdirectory marker creates
        for subdir in output_dir.iterdir():
            if subdir.is_dir():
                for f in subdir.iterdir():
                    f.unlink()
                subdir.rmdir()
        return True
    except FileNotFoundError:
        print("marker_single not found. Install with: pip install marker-pdf", file=sys.stderr)
        return False


def extract_epub(source: Path, output_dir: Path) -> bool:
    """Extract EPUB to markdown by parsing XHTML content."""
    output_file = output_dir / "full.md"
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


def extract_book(book: dict, source_file: Path, output_dir: Path) -> bool:
    """Extract a single book to markdown."""
    book_id = book["id"]
    book_output = output_dir / str(book_id)
    book_output.mkdir(parents=True, exist_ok=True)

    suffix = source_file.suffix.lower()

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


def main():
    """CLI entry point for extraction."""
    args = parse_args()

    config = load_config()
    library_path = expand_path(config["library_path"])
    output_path = expand_path(config["output_path"])

    output_path.mkdir(parents=True, exist_ok=True)

    books = get_calibre_books(library_path)

    for book in books:
        book_id = book["id"]
        title = book.get("title", "Unknown")

        # Filter by book ID if specified
        if args["book_ids"] and book_id not in args["book_ids"]:
            continue

        source_file = get_source_file(book)
        if not source_file or not source_file.exists():
            print(f"[{book_id}] {title}: No source file found, skipping")
            continue

        if not args["force"] and not needs_extraction(book, source_file, output_path):
            print(f"[{book_id}] {title}: Already extracted, skipping")
            continue

        if args["dry_run"]:
            print(f"[{book_id}] {title}: Would extract from {source_file.suffix}")
            continue

        print(f"[{book_id}] {title}: Extracting...")

        if extract_book(book, source_file, output_path):
            source_hash = compute_file_hash(source_file)
            update_calibre_extraction_state(library_path, book_id, source_hash)
            print(f"[{book_id}] {title}: Done")
        else:
            print(f"[{book_id}] {title}: Extraction failed", file=sys.stderr)


if __name__ == "__main__":
    main()
