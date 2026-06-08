# Kindle Screenshot Capture (Last Resort DRM Workaround)

**When to use**: Books with `ACCOUNT_SECRET` DRM that fail decryption with device serial alone. This is a manual, hands-on process - not automated.

## Prerequisites

- Kindle for Mac installed with book downloaded
- Python 3.12+ with dependencies:
  ```bash
  pip install pyobjc-framework-Quartz img2pdf
  ```
- Screen Recording permission granted to Terminal (System Settings → Privacy → Screen Recording)

## The Problem

Amazon uses two DRM schemes:

| Scheme | Key Required | Can Decrypt? |
|--------|--------------|--------------|
| `CLIENT_ID` only | Device serial (16 chars) | Yes - use `librarian-kindle-extract` |
| `ACCOUNT_SECRET` + `CLIENT_ID` | Device serial + Account key | No - firmware 5.18.5+ blocks extraction |

Books with `ACCOUNT_SECRET` DRM (often newer purchases) cannot be decrypted on macOS. This workflow captures screenshots for OCR as a workaround.

## Workflow

### Phase 1: Capture Screenshots (Interactive)

This requires your attention. The script will capture pages while you monitor for popups/issues.

```bash
# 1. Open Kindle for Mac to the FIRST page you want to capture
# 2. Run the capture script
python scripts/kindle_screenshot.py --book "Book Title" [--pages N]
```

**During capture:**
- Watch for popups, notes, or UI elements that interrupt
- If interrupted: Ctrl+C to stop, dismiss the popup, navigate to next page
- Resume: `--start-page N` where N is the next page number in sequence

**Output**: `~/data/librarian/source/kindle-captures/{book-slug}/page_NNNN.png`

### Phase 2: Combine to PDF

```bash
python scripts/kindle_screenshot.py --book "Book Title" --combine-only
```

**Output**: `~/data/librarian/source/kindle-captures/{book-slug}/combined.pdf`

### Phase 3: OCR with Marker

```bash
marker_single ~/data/librarian/source/kindle-captures/{book-slug}/combined.pdf \
  --output_dir ~/data/librarian/source/kindle-captures/{book-slug}/markdown/
```

**Output**: `~/data/librarian/source/kindle-captures/{book-slug}/markdown/{book-slug}.md`

### Phase 4: Ingest to Pipeline

Run the combined PDF through the normal extract → index pipeline (this supersedes
the manual `marker_single` step above — `librarian extract` produces the proper
artifact layout that the indexer expects):

```bash
# Extract the captured PDF into the converted/ artifacts dir
librarian extract ~/data/librarian/source/kindle-captures/{book-slug}/combined.pdf \
  -o ~/data/librarian/converted

# Index all unindexed extraction dirs (or pass the specific {hash} dir)
librarian index
```

## Script Reference

```
kindle_screenshot.py --book "Name" [options]

Options:
  --book, -b      Book name (required, used for folder name)
  --pages, -p     Number of pages to capture (omit for auto-detect via duplicates)
  --start-page    Resume from this page number (default: 1)
  --delay         Seconds between pages (default: 0.5)
  --combine       Combine to PDF after capture
  --combine-only  Only combine existing images (skip capture)
  --output-dir    Override base output directory
```

## Troubleshooting

### "Warning: pyobjc-framework-Quartz not installed"
```bash
pip install pyobjc-framework-Quartz
```

### Screen capture shows nothing / black screen
Grant Screen Recording permission: System Settings → Privacy & Security → Screen Recording → Terminal (or your terminal app)

### Page not turning
- Ensure Kindle window has focus
- Try increasing `--delay` to 0.8 or 1.0
- Check that no popup/dialog is blocking

### False end-of-book detection
Popups or UI elements can cause consecutive identical screenshots. Dismiss the popup, note the last good page, and resume with `--start-page`.

### Capture stopped early
Check `capture_metadata.json` in the output folder for status. Resume with `--start-page N` where N is `end_page + 1` from the last run.

## Example: Complete Capture Session

```bash
# Session 1: Start capture
python scripts/kindle_screenshot.py --book "The Fund Industry"
# ... captures 321 pages, popup appears, Ctrl+C

# Dismiss popup in Kindle, navigate to next page

# Session 2: Resume
python scripts/kindle_screenshot.py --book "The Fund Industry" --start-page 322
# ... captures to page 400, end of book detected

# Combine and OCR
python scripts/kindle_screenshot.py --book "The Fund Industry" --combine-only
marker_single ~/data/librarian/source/kindle-captures/the-fund-industry/combined.pdf \
  --output_dir ~/data/librarian/source/kindle-captures/the-fund-industry/markdown/

# Verify
ls ~/data/librarian/source/kindle-captures/the-fund-industry/markdown/
```

## Why This Works

Screenshots capture the rendered text, bypassing DRM entirely. Marker/Surya's OCR is high quality and runs locally (no API costs). The tradeoff is manual effort and slightly lower fidelity than native text extraction.

## Alternatives Considered

| Alternative | Why Not |
|-------------|---------|
| Windows + Kindle PC 2.8.0 | Requires Windows VM, complex setup |
| Commercial tools (Epubor) | $30-50, still may fail on new DRM |
| Wait for DeDRM update | Uncertain timeline, may never support ACCOUNT_SECRET |
