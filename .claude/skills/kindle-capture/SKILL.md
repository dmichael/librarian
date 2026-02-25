---
name: kindle-capture
description: Capture screenshots from Kindle for Mac when a book has DRM that cannot be decrypted. Use when extraction fails with ACCOUNT_SECRET DRM or when the user wants to capture a Kindle book.
argument-hint: [book-title]
---

# Kindle Screenshot Capture

Capture page-by-page screenshots from Kindle for Mac, combine into PDF, and ingest into the librarian pipeline. This is the DRM workaround for books that fail decryption.

## Prerequisites

Before starting, verify:
1. Kindle for Mac is open with the target book on the **first page** to capture
2. Screen Recording permission is granted to the terminal app (System Settings > Privacy > Screen Recording)
3. `pyobjc-framework-Quartz` is installed (`pip install pyobjc-framework-Quartz`)

Ask the user to confirm these before proceeding.

## Workflow

### Step 1: Capture

Run the capture script from the project venv. The user must monitor for popups/interruptions.

```bash
/Users/dmichael/projects/librarian/.venv/bin/python scripts/kindle_screenshot.py --book "$ARGUMENTS" --combine
```

If the user knows the page count, add `--pages N`. If resuming, add `--start-page N`.

**Important**: This is interactive. The user needs to watch for Kindle popups or dialogs that block page turns. If interrupted, they press Ctrl+C, dismiss the popup, and resume with `--start-page N`.

The `--combine` flag downscales Retina screenshots to 50% (plenty for OCR) and combines into a PDF.

### Step 2: Ingest

After capture + combine, the PDF will be at:
```
~/data/librarian/kindle-captures/{book-slug}/{book-slug}.pdf
```

Upload it to the librarian pipeline:
```bash
curl -F file=@~/data/librarian/kindle-captures/{book-slug}/{book-slug}.pdf \
     -F title="$ARGUMENTS" \
     http://agents.local:8811/upload
```

Or if running locally, use the MCP tool:
```
ingest_book(title="$ARGUMENTS", source_path="~/data/librarian/kindle-captures/{book-slug}/{book-slug}.pdf")
```

### Step 3: Extract and Index

Once ingested, run the standard pipeline:
```
extract_book(book_id=<id>)
# Poll with book_status(book_id=<id>) until extracted
index_book(book_id=<id>)
# Poll with book_status(book_id=<id>) until indexed
```

### Troubleshooting

- **Black/empty screenshots**: Terminal needs Screen Recording permission
- **Pages not turning**: Increase delay with `--delay 1.0`, ensure Kindle has focus
- **False end-of-book**: A popup caused duplicate frames. Note last good page, dismiss popup, resume with `--start-page`
- **Combine fails**: Install `img2pdf` (`pip install img2pdf`) or ensure ImageMagick is available

See `docs/kindle-screenshot-capture.md` for full reference.
