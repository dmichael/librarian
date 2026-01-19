#!/usr/bin/env python3
"""Automate Kindle for Mac screenshot capture for OCR processing.

This script captures screenshots from the Kindle for Mac app, page by page,
for later OCR processing with marker/surya.

Usage:
    # Capture a book (will prompt for page count if not specified)
    python kindle_screenshot.py --book "The Fund Industry"

    # Capture with known page count
    python kindle_screenshot.py --book "The Fund Industry" --pages 526

    # Resume from a specific page
    python kindle_screenshot.py --book "The Fund Industry" --pages 526 --start-page 100

Output:
    Screenshots saved to: {kindle_captures_path}/{book-name}/ (see config/settings.yaml)

For agents:
    1. Ensure Kindle for Mac is open with the book on the FIRST page to capture
    2. Run this script with --book and optionally --pages
    3. User can press Ctrl+C to stop early
    4. Process output with marker_single for OCR
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Try to load config, fall back to defaults
try:
    from librarian.config import expand_path, load_config
    _CONFIG = load_config()
except ImportError:
    _CONFIG = {}
    def expand_path(p):
        return Path(p).expanduser()


def get_captures_dir() -> Path:
    """Get kindle captures directory from config or default."""
    path = _CONFIG.get("kindle_captures_path", "~/data/librarian/kindle-captures")
    return expand_path(path)


def run_applescript(script: str) -> str:
    """Execute AppleScript and return output."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def is_kindle_running() -> bool:
    """Check if Kindle app is running."""
    result = subprocess.run(
        ["pgrep", "-x", "Kindle"],
        capture_output=True,
    )
    return result.returncode == 0


def activate_kindle():
    """Bring Kindle to foreground."""
    run_applescript('tell application "Amazon Kindle" to activate')
    time.sleep(0.5)


def get_window_id(app_name: str) -> int | None:
    """Get window ID for an application using Quartz."""
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID,
        )

        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        )
        for w in windows:
            if w.get("kCGWindowOwnerName") == app_name:
                return w.get("kCGWindowNumber")
    except ImportError:
        print("Warning: pyobjc-framework-Quartz not installed")
        print("Install with: pip install pyobjc-framework-Quartz")
    return None


def get_kindle_window_bounds() -> tuple[int, int, int, int] | None:
    """Get Kindle window position and size via System Events."""
    script = '''
        tell application "System Events"
            tell process "Kindle"
                set win to front window
                set pos to position of win
                set sz to size of win
                return (item 1 of pos as text) & "," & (item 2 of pos as text) & "," & (item 1 of sz as text) & "," & (item 2 of sz as text)
            end tell
        end tell
    '''
    result = run_applescript(script)
    if result:
        parts = result.split(",")
        if len(parts) == 4:
            return tuple(int(p) for p in parts)
    return None


def take_screenshot(output_path: Path):
    """Capture screenshot of Kindle window by window ID."""
    window_id = get_window_id("Kindle")
    if window_id:
        # Capture specific window by ID (like Cmd+Shift+4+Space)
        subprocess.run(
            ["screencapture", "-x", "-l", str(window_id), str(output_path)],
            check=True,
        )
    else:
        # Fallback: capture by region
        bounds = get_kindle_window_bounds()
        if bounds:
            x, y, w, h = bounds
            subprocess.run(
                ["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(output_path)],
                check=True,
            )
        else:
            # Last resort: full screen
            print("Warning: Could not get window bounds, capturing full screen")
            subprocess.run(
                ["screencapture", "-x", str(output_path)],
                check=True,
            )


def next_page():
    """Send right arrow key to advance page."""
    # Activate Kindle and send right arrow key
    run_applescript('''
        tell application "Amazon Kindle" to activate
        delay 0.2
        tell application "System Events"
            key code 124
        end tell
    ''')


def prev_page():
    """Send left arrow key to go back a page."""
    run_applescript('''
        tell application "Amazon Kindle" to activate
        delay 0.1
        tell application "System Events"
            tell process "Kindle"
                key code 123
            end tell
        end tell
    ''')


def slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    # Lowercase and replace spaces/special chars with hyphens
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug


def file_hash(path: Path) -> str:
    """Compute MD5 hash of a file for duplicate detection."""
    md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


def combine_images_to_pdf(image_dir: Path, output_pdf: Path) -> bool:
    """Combine PNG images into a single PDF."""
    images = sorted(image_dir.glob("*.png"))
    if not images:
        print("No images to combine")
        return False

    # Try using img2pdf (best quality, preserves resolution)
    try:
        import img2pdf
        with open(output_pdf, "wb") as f:
            f.write(img2pdf.convert([str(img) for img in images]))
        print(f"Combined {len(images)} images into {output_pdf}")
        return True
    except ImportError:
        pass

    # Fallback to ImageMagick
    result = subprocess.run(
        ["convert"] + [str(img) for img in images] + [str(output_pdf)],
        capture_output=True,
    )
    if result.returncode == 0:
        print(f"Combined {len(images)} images into {output_pdf}")
        return True

    print("Warning: img2pdf not installed, ImageMagick not found.")
    print("Install img2pdf: pip install img2pdf")
    return False


def save_metadata(output_dir: Path, book_name: str, pages_captured: int, start_page: int):
    """Save capture metadata for agent use."""
    metadata = {
        "book_name": book_name,
        "pages_captured": pages_captured,
        "start_page": start_page,
        "end_page": start_page + pages_captured - 1,
        "captured_at": datetime.now().isoformat(),
        "output_dir": str(output_dir),
        "status": "complete" if pages_captured > 0 else "empty",
    }

    metadata_path = output_dir / "capture_metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    return metadata_path


def capture_book(
    output_dir: Path,
    num_pages: int | None = None,
    delay: float = 0.5,
    start_page: int = 1,
    duplicate_threshold: int = 2,
) -> int:
    """Capture screenshots of a book page by page.

    Args:
        output_dir: Directory to save screenshots
        num_pages: Number of pages to capture (None = until Ctrl+C or end of book)
        delay: Delay between pages in seconds
        start_page: Starting page number for filenames
        duplicate_threshold: Stop after this many consecutive duplicate pages

    Returns:
        Number of pages captured
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if num_pages:
        print(f"Capturing up to {num_pages} pages to {output_dir}")
    else:
        print(f"Capturing pages to {output_dir}")

    print("Will auto-stop when end of book is detected (duplicate pages).")
    print("\nMake sure Kindle is open to the FIRST page you want to capture.")
    print("Press Ctrl+C to stop early.\n")

    activate_kindle()
    time.sleep(1)

    page_num = start_page
    max_pages = num_pages if num_pages else 10000  # Effectively unlimited
    last_hash = None
    duplicate_count = 0

    try:
        for i in range(max_pages):
            page_num = start_page + i
            output_path = output_dir / f"page_{page_num:04d}.png"

            # Take screenshot
            take_screenshot(output_path)

            # Check for duplicate (end of book detection)
            current_hash = file_hash(output_path)
            if current_hash == last_hash:
                duplicate_count += 1
                if duplicate_count >= duplicate_threshold:
                    # Remove duplicate files
                    for j in range(duplicate_count):
                        dup_path = output_dir / f"page_{page_num - j:04d}.png"
                        if dup_path.exists():
                            dup_path.unlink()
                    print(f"\nEnd of book detected at page {page_num - duplicate_count}")
                    break
            else:
                duplicate_count = 0
                last_hash = current_hash

            if num_pages:
                print(f"Captured page {page_num}/{start_page + num_pages - 1}")
            else:
                print(f"Captured page {page_num}")

            # Advance to next page
            next_page()

            # Wait for page turn animation
            time.sleep(delay)

    except KeyboardInterrupt:
        print(f"\nStopped at page {page_num}")

    # Count captured pages
    captured = list(output_dir.glob("*.png"))
    print(f"\nCaptured {len(captured)} pages total")

    return len(captured)


def main():
    parser = argparse.ArgumentParser(
        description="Capture Kindle book pages as screenshots for OCR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Capture a book (press Ctrl+C when done)
    %(prog)s --book "The Fund Industry"

    # Capture with known page count
    %(prog)s --book "The Fund Industry" --pages 526

    # Resume from page 100
    %(prog)s --book "The Fund Industry" --pages 426 --start-page 100

    # Combine existing screenshots into PDF
    %(prog)s --book "The Fund Industry" --combine-only
        """,
    )
    parser.add_argument(
        "--book", "-b",
        type=str,
        required=True,
        help="Book name (used for output folder name)",
    )
    parser.add_argument(
        "--pages", "-p",
        type=int,
        default=None,
        help="Number of pages to capture (omit to capture until Ctrl+C)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help="Base output directory (default: from config kindle_captures_path)",
    )
    parser.add_argument(
        "--delay", "-d",
        type=float,
        default=0.5,
        help="Delay between pages in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--start-page", "-s",
        type=int,
        default=1,
        help="Starting page number for filenames (default: 1)",
    )
    parser.add_argument(
        "--combine",
        action="store_true",
        help="Combine captured images into a PDF after capture",
    )
    parser.add_argument(
        "--combine-only",
        action="store_true",
        help="Only combine existing images into PDF (skip capture)",
    )

    args = parser.parse_args()

    # Build output path
    base_dir = args.output_dir or get_captures_dir()
    book_slug = slugify(args.book)
    output_dir = base_dir / book_slug

    print(f"Book: {args.book}")
    print(f"Output: {output_dir}")

    # Combine-only mode
    if args.combine_only:
        pdf_path = output_dir / f"{book_slug}.pdf"
        if combine_images_to_pdf(output_dir, pdf_path):
            print(f"\nReady for pipeline:")
            print(f"  cp {pdf_path} ~/data/librarian/source/")
            print(f"  calibredb add ~/data/librarian/source/{book_slug}.pdf --library-path ~/data/librarian/calibre")
        return

    # Check Kindle is running
    if not is_kindle_running():
        print("\nError: Kindle is not running.")
        print("Please open Kindle for Mac and navigate to the book's first page.")
        sys.exit(1)

    # Capture pages
    captured = capture_book(
        output_dir=output_dir,
        num_pages=args.pages,
        delay=args.delay,
        start_page=args.start_page,
    )

    # Save metadata
    if captured > 0:
        metadata_path = save_metadata(output_dir, args.book, captured, args.start_page)
        print(f"Metadata saved to: {metadata_path}")

    # Combine if requested
    if args.combine and captured > 0:
        pdf_path = output_dir / f"{book_slug}.pdf"
        combine_images_to_pdf(output_dir, pdf_path)
        print(f"\nReady for pipeline:")
        print(f"  cp {pdf_path} ~/data/librarian/source/")
        print(f"  calibredb add ~/data/librarian/source/{book_slug}.pdf --library-path ~/data/librarian/calibre")


if __name__ == "__main__":
    main()
