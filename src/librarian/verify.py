"""Post-indexing QA checks for a book.

Each dimension returns green/yellow/red with details and issues. All vector
store access goes through the LibrarianVectorStore protocol (one bulk
get_documents_by_filter per collection; analysis happens in Python), so
verification works on any backend.
"""

import logging
import random
import re
from collections import Counter
from pathlib import Path

from librarian.db import Book, session_scope

log = logging.getLogger(__name__)


def _assess(status: str, details: dict, issues: list[str]) -> dict:
    """Build a verification dimension result."""
    return {"status": status, "details": details, "issues": issues}


def _check_garbled(text: str) -> list[str]:
    """Check a text sample for OCR quality issues."""
    issues = []
    words = text.split()
    if not words:
        return ["Empty text"]

    # Average word length — very short suggests garbled extraction
    avg_len = sum(len(w) for w in words) / len(words)
    if avg_len < 2.5:
        issues.append(f"Very short average word length ({avg_len:.1f})")

    # Excessive special characters (non-alphanumeric, non-punctuation)
    alpha_count = sum(1 for c in text if c.isalnum() or c.isspace())
    alpha_ratio = alpha_count / len(text) if text else 0
    if alpha_ratio < 0.6:
        issues.append(f"Low alphanumeric ratio ({alpha_ratio:.0%})")

    # Repeated characters (e.g., "aaaa" or "????")
    repeated = re.findall(r'(.)\1{4,}', text)
    if repeated:
        issues.append(f"Repeated character runs: {''.join(set(repeated))}")

    # Encoding artifacts
    encoding_markers = ['â€™', 'â€"', 'â€œ', 'Ã©', 'Ã¡', 'ï¬', '�']
    found = [m for m in encoding_markers if m in text]
    if found:
        issues.append(f"Encoding artifacts: {', '.join(found[:3])}")

    return issues


def _as_int(value) -> int | None:
    """Coerce a metadata value to int, treating null markers as missing."""
    if value is None or value == "" or value == "null":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_LANDMARK_KEYWORDS = {
    "table_of_contents": ["table of contents", "contents"],
    "bibliography": ["bibliography", "references cited", "works cited"],
    "index": [r"\bindex\b"],  # word boundary to avoid matching "indexing" etc.
    "appendix": ["appendix"],
    "glossary": ["glossary"],
    "preface": ["preface", "foreword"],
    "introduction": ["introduction"],
}


def verify_book(config: dict, book_id: int) -> dict:
    """Run all QA dimensions for one book. Returns the verification report."""
    from librarian.config import expand_path
    from librarian.index import load_extracted_blocks, load_extracted_book
    from librarian.structure import (
        extract_structure_from_blocks,
        parse_structure,
        validate_structure,
    )
    from librarian.structure_audit import audit_structure_with_llm
    from librarian.vectorstore import get_collection_names, get_vector_store

    results = {}

    with session_scope(config) as session:
        book = session.query(Book).filter(Book.id == book_id).first()
        if not book:
            return {"success": False, "error": f"Book {book_id} not found"}
        book_title = book.title
        book_meta = {
            "title": book.title,
            "authors": book.authors or [],
            "subjects": book.subjects or [],
            "library": book.library,
            "format": book.format,
            "status": book.status,
            "extraction_duration_s": book.extraction_duration_s,
        }
        source_path = book.source_path
        converted_path = book.converted_path

    # ── 1. Metadata ─────────────────────────────────────────────
    meta_issues = []
    if not book_meta["title"]:
        meta_issues.append("Missing title")
    if not book_meta["authors"]:
        meta_issues.append("Missing authors")
    if not book_meta["subjects"]:
        meta_issues.append("Missing subjects (use update_book to tag)")
    if not book_meta["library"]:
        meta_issues.append("Missing library (use update_book to assign)")

    meta_status = "green"
    if not book_meta["title"] or not book_meta["authors"]:
        meta_status = "red"
    elif not book_meta["subjects"] or not book_meta["library"]:
        meta_status = "yellow"

    results["metadata"] = _assess(meta_status, book_meta, meta_issues)

    # ── 2. Source Files ───────────────────────────────────────
    src_issues = []
    src_details = {}

    if source_path:
        src = Path(source_path)
        src_details["source_path"] = source_path
        if src.exists():
            src_details["source_exists"] = True
            src_details["source_size_bytes"] = src.stat().st_size
        else:
            src_details["source_exists"] = False
            src_issues.append(f"Source file missing: {source_path}")
    else:
        src_details["source_exists"] = False
        src_issues.append("No source_path set")

    if converted_path:
        conv = Path(converted_path)
        src_details["converted_path"] = converted_path
        if conv.exists():
            src_details["converted_exists"] = True
        else:
            src_details["converted_exists"] = False
            src_issues.append(f"Converted file missing: {converted_path}")

    src_status = "green"
    if not source_path or not Path(source_path).exists():
        src_status = "red"
    elif src_issues:
        src_status = "yellow"

    results["source_files"] = _assess(src_status, src_details, src_issues)

    # ── 3. Load extracted content ───────────────────────────────
    output_path = expand_path(config["output_path"])
    book_dir = output_path / str(book_id)

    if not book_dir.exists():
        results["extraction"] = _assess("red", {}, ["No extracted content directory found"])
        return {"success": True, "book_id": book_id, "verification": results}

    blocks = load_extracted_blocks(book_dir)
    content, raw_content = load_extracted_book(book_dir)

    if not content:
        results["extraction"] = _assess("red", {}, ["No extracted markdown found"])
        return {"success": True, "book_id": book_id, "verification": results}

    # ── 4. Structure / Chapters ─────────────────────────────────
    if blocks:
        structure = extract_structure_from_blocks(blocks, title=book_title or "")
        audit = audit_structure_with_llm(structure, blocks, book_title or "", config)
        structure = audit.structure
        pages = [b.get("page") for b in blocks if b.get("page")]
        total_pages = (max(pages) - min(pages) + 1) if pages else None
        structure_source = "blocks+llm" if audit.applied else "blocks"
    else:
        structure = parse_structure(raw_content, title=book_title or "")
        total_pages = None
        structure_source = "markdown"

    validation = validate_structure(structure, total_pages)
    ch_count = validation["chapter_count"]

    struct_issues = list(validation.get("warnings", []))
    if structure_source == "markdown":
        struct_issues.append(
            "Using markdown fallback (no JSON blocks) — page numbers may be unreliable"
        )

    # Count sections. Articles/papers have sections but no chapters; a
    # section-only document is well-structured, not broken. Works for both
    # block-based (block_to_section) and chaptered (chapters[].sections).
    section_titles = set(structure.block_to_section.values())
    for ch in structure.chapters:
        section_titles.update(s.title for s in ch.sections if s.title)
    section_count = len(section_titles)

    if ch_count == 0 and section_count == 0:
        struct_issues.append(
            "No chapters or sections detected — headers may not match known patterns"
        )

    chapters_without_title = [ch for ch in structure.chapters if not ch.title]
    if chapters_without_title:
        struct_issues.append(
            f"{len(chapters_without_title)} chapters missing titles: "
            + ", ".join(f"Ch {ch.number}" for ch in chapters_without_title[:5])
        )

    # Red only when there's no structure at all; a section-only doc is fine.
    struct_status = "green"
    if ch_count == 0 and section_count == 0:
        struct_status = "red"
    elif struct_issues:
        struct_status = "yellow"

    chapter_details = [
        {
            "number": ch.number,
            "title": ch.title or "(untitled)",
            "page_start": ch.page_start,
            "page_end": ch.page_end,
            "sections": len(ch.sections),
        }
        for ch in structure.chapters
    ]

    results["structure"] = _assess(struct_status, {
        "source": structure_source,
        "chapter_count": ch_count,
        "section_count": section_count,
        "chapters_with_pages": validation["chapters_with_pages"],
        "page_coverage": round(validation["page_coverage"], 2),
        "total_pages": total_pages,
        "chapters": chapter_details,
    }, struct_issues)

    # ── 5. Chunk analysis from the vector store ─────────────────
    store = get_vector_store(config)
    collections = get_collection_names(config)

    rows = store.get_documents_by_filter(collections["full"], {"book_id": book_id})
    chunk_count = len(rows)

    if chunk_count == 0:
        results["completeness"] = _assess("red", {"chunks": 0}, ["No chunks in vector store"])
        return {"success": True, "book_id": book_id, "verification": results}

    chunk_pages = []
    chunks_no_page = 0
    chapter_nums = set()
    chunks_with_chapter = 0
    chunks_with_section = 0
    block_type_dist = Counter()

    for _text, meta in rows:
        page = _as_int(meta.get("page"))
        if page is None:
            chunks_no_page += 1
        else:
            chunk_pages.append(page)

        ch_num = _as_int(meta.get("chapter_num"))
        if ch_num is not None:
            chunks_with_chapter += 1
            chapter_nums.add(ch_num)

        if meta.get("section_title"):
            chunks_with_section += 1

        block_type_dist[meta.get("block_type") or "Unknown"] += 1

    min_page = min(chunk_pages) if chunk_pages else None
    max_page = max(chunk_pages) if chunk_pages else None
    distinct_pages = len(set(chunk_pages))
    indexed_chapter_nums = sorted(chapter_nums)

    chapter_coverage = chunks_with_chapter / chunk_count
    section_coverage = chunks_with_section / chunk_count

    completeness_issues = []
    if chunks_no_page and chunks_no_page > chunk_count * 0.3:
        completeness_issues.append(
            f"{chunks_no_page}/{chunk_count} chunks missing page numbers"
        )
    # Chaptered docs should carry chapter metadata; chapterless docs
    # (articles) should carry section metadata. Only flag the one that applies.
    if ch_count > 0:
        if chapter_coverage < 0.5:
            completeness_issues.append(
                f"Only {chapter_coverage:.0%} of chunks have chapter metadata "
                f"({chunks_with_chapter}/{chunk_count})"
            )
    elif section_coverage < 0.5:
        completeness_issues.append(
            f"Only {section_coverage:.0%} of chunks have section metadata "
            f"({chunks_with_section}/{chunk_count})"
        )
    page_span = (max_page - min_page + 1) if (min_page is not None and max_page is not None) else None
    if page_span and distinct_pages and distinct_pages < page_span * 0.5:
        completeness_issues.append(
            f"Only {distinct_pages}/{page_span} distinct pages represented in chunks"
        )

    # Check for page gaps within the actual page range
    if min_page is not None and max_page is not None:
        missing = set(range(min_page, max_page + 1)) - set(chunk_pages)
        if len(missing) > 5:
            missing_sorted = sorted(missing)
            gaps = []
            gap_start = gap_end = missing_sorted[0]
            for p in missing_sorted[1:]:
                if p == gap_end + 1:
                    gap_end = p
                else:
                    gaps.append((gap_start, gap_end))
                    gap_start = gap_end = p
            gaps.append((gap_start, gap_end))
            gap_strs = [f"{s}-{e}" if s != e else str(s) for s, e in gaps[:5]]
            completeness_issues.append(
                f"{len(missing)} pages missing from chunks. Gaps: {', '.join(gap_strs)}"
                + (" ..." if len(gaps) > 5 else "")
            )

    comp_status = "green"
    if chunk_count < 10 or chapter_coverage < 0.3:
        comp_status = "red"
    elif completeness_issues:
        comp_status = "yellow"

    results["completeness"] = _assess(comp_status, {
        "total_chunks": chunk_count,
        "chunks_with_chapter": chunks_with_chapter,
        "chapter_coverage": f"{chapter_coverage:.0%}",
        "chunks_with_section": chunks_with_section,
        "section_coverage": f"{section_coverage:.0%}",
        "indexed_chapters": indexed_chapter_nums,
        "page_range": f"{min_page}-{max_page}" if min_page is not None else "unknown",
        "distinct_pages": distinct_pages,
        "total_pages": total_pages,
        "chunks_missing_page": chunks_no_page,
        "block_types": dict(block_type_dist.most_common()),
    }, completeness_issues)

    # ── 6. OCR Quality (sample chunks) ──────────────────────────
    texts = [text for text, _meta in rows if text]
    sample_texts = random.sample(texts, min(20, len(texts)))

    ocr_issues = []
    garbled_count = 0
    checked_count = 0
    for text in sample_texts:
        # Skip very short chunks (verse numbers, page markers, etc.)
        if len(text.strip()) < 30:
            continue
        checked_count += 1
        chunk_issues = _check_garbled(text)
        if chunk_issues:
            garbled_count += 1
            if len(ocr_issues) < 3:  # Only report first 3
                preview = text[:80].replace('\n', ' ')
                ocr_issues.append(f"Sample: '{preview}...' — {'; '.join(chunk_issues)}")

    garbled_ratio = garbled_count / checked_count if checked_count else 0
    ocr_status = "green"
    if garbled_ratio > 0.3:
        ocr_status = "red"
    elif garbled_ratio > 0.1:
        ocr_status = "yellow"

    results["ocr_quality"] = _assess(ocr_status, {
        "samples_checked": checked_count,
        "samples_with_issues": garbled_count,
        "issue_ratio": f"{garbled_ratio:.0%}",
    }, ocr_issues)

    # ── 7. Landmarks ────────────────────────────────────────────
    header_texts = [
        text for text, meta in rows
        if text and meta.get("block_type") == "SectionHeader"
    ]

    landmarks_found = {}
    for landmark, keywords in _LANDMARK_KEYWORDS.items():
        pattern = re.compile("|".join(keywords), re.IGNORECASE)
        if any(pattern.search(t) for t in header_texts):
            landmarks_found[landmark] = "found_in_headers"
        elif any(pattern.search(t) for t in texts):
            landmarks_found[landmark] = "found_in_text"

    landmark_issues = []
    # Most books should have at least an introduction
    if "introduction" not in landmarks_found and "preface" not in landmarks_found:
        landmark_issues.append("No introduction or preface found")
    if "table_of_contents" not in landmarks_found:
        landmark_issues.append("No table of contents found (may be normal for some books)")

    landmark_status = "green" if landmarks_found else "yellow"

    results["landmarks"] = _assess(landmark_status, {
        "found": landmarks_found,
    }, landmark_issues)

    # ── 8. Equations ────────────────────────────────────────────
    eq_count = len(store.get_documents_by_filter(
        collections["equations"], {"book_id": book_id}
    ))

    latex_in_content = bool(re.search(r'\$\$.+?\$\$', raw_content, re.DOTALL)) if raw_content else False

    eq_issues = []
    if latex_in_content and eq_count == 0:
        eq_issues.append("LaTeX equations found in content but none indexed")
    eq_status = "yellow" if eq_issues else "green"

    results["equations"] = _assess(eq_status, {
        "indexed_equations": eq_count,
        "latex_in_content": latex_in_content,
    }, eq_issues)

    # ── 9. Summaries (book / chapter / section hierarchy) ───────
    summary_rows = store.get_documents_by_filter(
        collections["chapters"], {"book_id": book_id}
    )
    by_level = Counter(
        # Pre-hierarchy indexes have no level field; those nodes are chapters.
        meta.get("level") or "chapter"
        for _text, meta in summary_rows
    )
    book_summaries = by_level.get("book", 0)
    chapter_summaries = by_level.get("chapter", 0)
    section_summaries = by_level.get("section", 0)

    sum_issues = []
    if book_summaries == 0:
        sum_issues.append(
            "No book-level summary (indexed before the summary hierarchy — re-index to add)"
        )
    if ch_count > 0 and chapter_summaries == 0:
        sum_issues.append(
            f"Structure has {ch_count} chapters but none are indexed with summaries"
        )
    elif ch_count > 0 and chapter_summaries < ch_count:
        sum_issues.append(f"Only {chapter_summaries}/{ch_count} chapters have summaries")
    elif ch_count == 0 and section_count > 0 and section_summaries == 0 and book_summaries == 0:
        sum_issues.append(
            f"{section_count} sections detected but no summaries at any level"
        )

    sum_status = "green"
    if ch_count > 0 and chapter_summaries == 0:
        sum_status = "red"
    elif sum_issues:
        sum_status = "yellow"

    results["summaries"] = _assess(sum_status, {
        "detected_chapters": ch_count,
        "detected_sections": section_count,
        "book_summaries": book_summaries,
        "chapter_summaries": chapter_summaries,
        "section_summaries": section_summaries,
    }, sum_issues)

    # ── Overall ─────────────────────────────────────────────────
    statuses = [r["status"] for r in results.values()]
    if "red" in statuses:
        overall = "red"
    elif "yellow" in statuses:
        overall = "yellow"
    else:
        overall = "green"

    all_issues = [
        f"[{dim}] {issue}"
        for dim, result in results.items()
        for issue in result["issues"]
    ]

    return {
        "success": True,
        "book_id": book_id,
        "title": book_title,
        "overall_status": overall,
        "verification": results,
        "all_issues": all_issues,
    }
