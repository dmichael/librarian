# Feature: Kindle DRM Extraction

## Summary

Process Kindle books from the source folder, strip DRM, and convert to EPUB for Calibre ingestion.

## Problem

Kindle books come in various DRM-protected formats (KFX, AZW3, AZW, MOBI). Calibre needs DRM-free files to process them. Currently this relies on a manually-patched DeDRM plugin during Calibre import, which:

- Fails silently on some books (different DRM key issues)
- Requires the Kindle serial to be configured correctly
- Has no clear feedback on what worked vs what failed
- Doesn't handle the KFX format reliably without patches

## Current Workflow

```
~/data/librarian/source/kindle/{SERIAL}/
    ├── book1.azw3
    ├── book2.kfx
    └── ...
         │
         ▼ (manual: librarian-ingest → calibredb add)
         │
    Calibre (DeDRM plugin attempts decryption)
         │
         ▼
    Success or silent failure
```

## Requirements

- [ ] Process all Kindle formats: KFX, AZW3, AZW, MOBI
- [ ] Strip DRM using Kindle serial number
- [ ] Convert to EPUB (Calibre's preferred format)
- [ ] Report clear success/failure per book
- [ ] Handle books that fail decryption gracefully (log, don't crash)
- [ ] Work as a CLI command: `librarian-kindle-extract`

## Acceptance Criteria

### Inputs
- **Source folder**: `~/data/librarian/source/kindle/{SERIAL}/`
- **Extensions**: `.kfx`, `.azw`, `.azw3`, `.azw8`, `.mobi`, `.prc`
- **Recursion**: No (flat directory only)
- **Ignore**: `.sdr` folders, files already processed

### Outputs
- **Output folder**: `~/data/librarian/source/kindle-extracted/`
- **Naming**: `{Title} - {Author}.epub` (Calibre's naming from metadata)
- **Converted means**: file exists, non-zero size, can be opened by `ebook-viewer` or imported via `calibredb add`

### Success Condition
**For every Kindle book file in the source folder, produce a corresponding DRM-free .epub in the output folder.**

- Exit 0 only when ALL books succeed
- Exit non-zero if ANY book fails
- Currently: 8 books in source → must produce 8 EPUBs

### Failure Policy
- Partial success is NOT acceptable for feature completion
- Agent must iterate until 100% success rate
- Each failed book must have a documented reason and attempted fix

### Verification Steps
```bash
# 1. Run extraction
librarian-kindle-extract

# 2. Check results
# Expected: Succeeded == 8, Failed == 0

# 3. Verify output count
ls ~/data/librarian/source/kindle-extracted/*.epub | wc -l
# Expected: 8

# 4. Verify each EPUB is readable
for f in ~/data/librarian/source/kindle-extracted/*.epub; do
  ebook-viewer --detach "$f" && sleep 1 && pkill -f ebook-viewer
done
# Expected: No errors, each opens successfully
```

### Current Status
- **Passing**: 5/8 books
- **Failing**: 3 books (DRM key issues)
- **Feature complete**: NO — must reach 8/8

## Decisions

- **Output**: Staging folder (`source/kindle-extracted/`) — don't modify originals
- **Sources**: Discover and support all viable sources (device copy, Kindle for Mac, etc.)
- **Failures**: Log errors with detail, continue processing, iterate on failures until solved

## Approach

This is exploratory. The goal is to discover what works:

1. Try multiple DRM removal approaches
2. Capture detailed errors for failures
3. Iterate on failed books until we find solutions
4. Document what works for each book type/source

Failed books are the iteration target, not edge cases to ignore.

## Out of Scope

- Getting books from Kindle device to source folder (manual via OpenMTP/Finder)
- Calibre import itself (handled by existing `librarian-ingest`)
- Non-Kindle DRM schemes (Kobo, Adobe, etc.)

## Open Questions

- Use DeDRM as a library, or shell out to existing tools?
- What are all the possible key sources (device serial, Kindle for Mac keys, etc.)?
- Are there alternative tools beyond DeDRM worth trying?
