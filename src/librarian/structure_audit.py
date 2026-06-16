"""LLM post-processing for document structure.

The deterministic parser is the first draft.  This module asks one LLM to
interpret the extracted heading list, then applies the result only if the
returned block references are internally consistent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import librarian.llm as llm
from librarian.document_metadata import now_iso
from librarian.structure import Chapter, DocumentStructure, Section

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructureAuditResult:
    """Result of an LLM structure audit."""

    structure: DocumentStructure
    applied: bool
    reason: str
    # Block where back matter begins (index/glossary/about-the-author/etc.) as
    # judged by the model, or None if the chapters run to the end. Consumed by
    # trim_back_matter so the boundary is model-decided, not a title heuristic.
    body_end: int | None = None


def audit_structure_with_llm(
    structure: DocumentStructure,
    blocks: list[dict] | None,
    title: str,
    config: dict,
    artifact_dir: Path | None = None,
) -> StructureAuditResult:
    """Run one LLM structure audit and return the structure to index.

    The LLM decides whether the current outline should be replaced.  Local code
    only checks referential integrity: proposed starts must be real heading
    blocks, unique, and ordered.

    When artifact_dir is given, the full exchange (prompt, raw response, the
    model's reasoning, and the local decision) is persisted to
    artifact_dir/raw/llm/structure_audit.json for offline analysis.
    """
    if not blocks:
        return StructureAuditResult(structure, False, "no blocks available")

    headings = _heading_records(blocks)
    if not headings:
        return StructureAuditResult(structure, False, "no headings available")

    # Chapters sit at the document's top heading level. Restrict candidates to
    # that level so the list is short enough to keep peeks (the signal that
    # separates real chapters from sections/appendices) — the model judges
    # ~chapters, not every heading. Fall back to all headings if level info is
    # missing or the filter would empty the list.
    chapter_level = _chapter_level(headings)
    candidates = headings
    if chapter_level is not None:
        at_level = [h for h in headings if h.get("level") == chapter_level]
        if at_level:
            candidates = at_level

    prompt, trim_note = _fit_audit_prompt(title, candidates, structure)
    if trim_note:
        log.warning(
            "Structure audit prompt trimmed to fit the model context (%s) for %r",
            trim_note, title,
        )
    response = llm.complete(prompt, config, max_tokens=2048, timeout=180.0)
    if not response.strip():
        log.warning(
            "Structure audit got an empty response for %r; book falls back to "
            "block/section structure with no chapters (prompt was %d chars)",
            title, len(prompt),
        )

    result, reasoning = _decide(structure, blocks, title, response)
    _save_audit_artifact(artifact_dir, config, prompt, response, reasoning, result)
    return result


def _decide(
    structure: DocumentStructure,
    blocks: list[dict],
    title: str,
    response: str,
) -> tuple[StructureAuditResult, str]:
    """Turn the raw LLM response into a validated audit result."""
    if not response.strip():
        return StructureAuditResult(structure, False, "empty LLM response"), ""

    try:
        payload = _loads_json_object(response)
    except ValueError as exc:
        log.warning("Structure audit returned invalid JSON: %s", exc)
        return StructureAuditResult(structure, False, "invalid JSON"), ""

    reasoning = str(payload.get("reasoning") or "")
    action = str(payload.get("action", "")).strip().lower()
    chapters = payload.get("chapters") or []
    if action == "no_change" or not chapters:
        return StructureAuditResult(structure, False, "LLM requested no change"), reasoning

    if action not in {"replace_structure", "replace_chapters", "repair"}:
        return StructureAuditResult(structure, False, f"unsupported action: {action}"), reasoning

    validated = _validate_chapters(chapters, blocks)
    if not validated:
        return StructureAuditResult(structure, False, "invalid chapter references"), reasoning

    repaired = _structure_from_chapters(blocks, title, validated)
    body_end = _validate_body_end(payload.get("body_end_block_idx"), blocks, validated)
    return (
        StructureAuditResult(repaired, True, f"applied {len(validated)} chapters", body_end),
        reasoning,
    )


def _validate_body_end(
    value: Any, blocks: list[dict], validated: list[dict[str, Any]]
) -> int | None:
    """Validate the model's back-matter boundary: a real heading after the chapters."""
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= idx < len(blocks):
        return None
    if blocks[idx].get("block_type") != "SectionHeader":
        return None
    last_start = validated[-1]["start"] if validated else -1
    if idx <= last_start:
        return None
    return idx


def _save_audit_artifact(
    artifact_dir: Path | None,
    config: dict,
    prompt: str,
    response: str,
    reasoning: str,
    result: StructureAuditResult,
) -> None:
    """Persist the audit exchange for offline analysis. Never raises."""
    if artifact_dir is None:
        return
    llm_config = config.get("classification", {})
    payload = {
        "audited_at": now_iso(),
        "provider": llm_config.get("provider", llm.DEFAULT_PROVIDER),
        "model": llm_config.get("model", llm.DEFAULT_MODEL),
        "applied": result.applied,
        "reason": result.reason,
        "reasoning": reasoning,
        "chapters": [
            {"number": ch.number, "title": ch.title, "page_start": ch.page_start}
            for ch in result.structure.chapters
        ],
        "prompt": prompt,
        "response": response,
    }
    try:
        out_dir = artifact_dir / "raw" / "llm"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "structure_audit.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
    except OSError as exc:
        log.warning("Could not save structure audit artifact: %s", exc)


PEEK_CHARS = 180

# The audit prompts serialize headings with body peeks, which run long for a book
# with hundreds of headings. Keep a prompt under this char budget so it fits the
# aux's 16K-token window after the answer (this content is ~4 chars/token). The
# _fit_* helpers shrink peeks first so every heading still reaches the model. The
# whole-book chapter audit hits this cap (and runs peekless on heading-heavy
# books, which is what keeps its chapter split clean); the per-chapter section
# audit is far smaller and fits with peeks intact.
PROMPT_CHAR_BUDGET = 38000


def _heading_records(blocks: list[dict]) -> list[dict[str, Any]]:
    records = []
    for idx, block in enumerate(blocks):
        if block.get("block_type") != "SectionHeader":
            continue
        text = _clean_heading(block.get("text") or "")
        if not text:
            continue
        records.append({
            "block_idx": idx,
            "page": block.get("page"),
            "level": _heading_level(block),
            "text": text[:240],
            "peek": _content_peek(blocks, idx),
        })
    return records


def _chapter_level(records: list[dict[str, Any]]) -> int | None:
    """The shallowest heading level that recurs in the body (>= 3 times).

    Chapters sit at the document's top heading level. Restricting the audit's
    candidates to that level keeps the list short enough to retain peeks (which
    is what lets the model separate real chapters from sections and appendices),
    and adapts to books whose chapters sit at level 2 instead of 1.
    """
    counts: dict[int, int] = {}
    for r in records:
        level = r.get("level")
        if level is not None:
            counts[level] = counts.get(level, 0) + 1
    for level in sorted(counts):
        if counts[level] >= 3:
            return level
    return None


def _content_peek(blocks: list[dict], heading_idx: int) -> str:
    """First PEEK_CHARS of body text after a heading.

    This is what disambiguates a heading's *role*: a true chapter opener is
    followed by narrative prose, while the same title in the table of contents,
    endnotes, or bibliography is followed by more headings, page numbers, or
    numbered citations. Heading text alone cannot make that distinction.
    """
    parts: list[str] = []
    length = 0
    for block in blocks[heading_idx + 1:]:
        if block.get("block_type") == "SectionHeader":
            break
        text = (block.get("text") or "").strip()
        if not text:
            continue
        parts.append(text)
        length += len(text) + 1
        if length >= PEEK_CHARS:
            break
    return " ".join(parts)[:PEEK_CHARS]


def _build_prompt(
    title: str,
    headings: list[dict[str, Any]],
    structure: DocumentStructure,
) -> str:
    current = [
        {"number": ch.number, "title": ch.title, "page_start": ch.page_start}
        for ch in structure.chapters
    ]
    payload = {
        "title": title,
        "current_chapters": current,
        "headings": headings,
    }
    return (
        "You are auditing the structure of an extracted document for indexing.\n"
        "Given the ordered SectionHeader blocks, decide whether the book has "
        "chapter starts that should replace the current chapter list.\n\n"
        "Each heading carries a 'peek': the first body text that follows it. "
        "Use it to judge the heading's role — a true chapter opener is followed "
        "by narrative prose; the same title in the table of contents, endnotes, "
        "or bibliography is followed by page numbers or numbered citations.\n\n"
        "Return JSON only. Use one of these shapes:\n"
        '{"action":"no_change","chapters":[],"reasoning":"one short paragraph"}\n'
        "or\n"
        '{"action":"replace_structure","chapters":[{"number":1,'
        '"title":"Chapter title","start_block_idx":123,'
        '"evidence":"heading text copied from the provided heading"}],'
        '"body_end_block_idx":456,"reasoning":"one short paragraph"}\n\n'
        "Rules:\n"
        "- Set body_end_block_idx to the block_idx of the first heading that "
        "begins BACK MATTER — an index, glossary, endnotes or bibliography, "
        "about-the-author, colophon, license, or trailing boilerplate — judged by "
        "its peek (page numbers, citations, an author bio, or boilerplate, not "
        "narrative prose). Use null if the chapters run to the end of the book.\n"
        "- Use actual body chapter starts, not table-of-contents entries.\n"
        "- Do not use notes/endnotes/bibliography headings as chapter starts: "
        "if a heading's peek is dominated by numbered citations or references, "
        "it is back matter even when it is labeled 'CHAPTER N'. Prefer the "
        "body heading whose peek is prose, even if it lacks a chapter number.\n"
        "- Only use start_block_idx values present in the provided headings.\n"
        "- Titles should be copied or lightly normalized from the same or adjacent headings.\n"
        "- If the current structure is already correct, return no_change.\n"
        "- In 'reasoning', explain which candidate headings you weighed and why "
        "you chose or rejected them.\n\n"
        f"Document data:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _fit_audit_prompt(
    title: str,
    headings: list[dict[str, Any]],
    structure: DocumentStructure,
) -> tuple[str, str | None]:
    """Build the audit prompt, keeping it under PROMPT_CHAR_BUDGET.

    Peeks are the bulk of the prompt, so shrink them (largest cap that still
    fits wins) before dropping any headings. Returns (prompt, note); note
    describes the trimming, or is None when the full prompt already fit.
    """
    prompt = _build_prompt(title, headings, structure)
    if len(prompt) <= PROMPT_CHAR_BUDGET:
        return prompt, None
    for cap in (120, 96, 64, 40, 0):
        trimmed = [{**h, "peek": h["peek"][:cap]} for h in headings]
        prompt = _build_prompt(title, trimmed, structure)
        if len(prompt) <= PROMPT_CHAR_BUDGET:
            return prompt, f"peeks capped at {cap} chars"
    # Even peekless heading text overflows (hundreds of headings): keep as many
    # as fit, estimated from per-heading cost so this stays O(n).
    peekless = [{**h, "peek": ""} for h in headings]
    base = len(_build_prompt(title, [], structure))
    per = max(1, (len(_build_prompt(title, peekless[:20], structure)) - base) // 20)
    keep = max(1, (PROMPT_CHAR_BUDGET - base) // per)
    kept = peekless[:keep]
    return _build_prompt(title, kept, structure), (
        f"peeks dropped and headings truncated {len(headings)}->{len(kept)}"
    )


def _heading_level(block: dict) -> int | None:
    """Marker encodes heading depth as <hN> in the block html."""
    m = re.search(r"<h(\d)", block.get("html", "") or "")
    return int(m.group(1)) if m else None


def _section_audit_prompt(chapter_title: str, candidates: list[dict[str, Any]]) -> str:
    return (
        "List the real sections of this one book chapter.\n"
        f'Chapter: "{chapter_title}"\n'
        "Each heading below has a 'level' (1=top-most, larger=deeper), the heading "
        "'text', and a 'peek' of the body text right after it. A real section is a "
        "top section-level heading (usually level 2) whose peek is narrative prose. "
        "Deeper headings (larger level numbers) are sub-points, list items, "
        "figure/table captions, or glossary/metadata lines — they are NOT sections; "
        "omit them. Also omit table-of-contents and bibliography entries (peek is "
        "page numbers or numbered citations).\n"
        'Return JSON only: {"sections":[{"title":"...","start_block_idx":N}],'
        '"reasoning":"one short paragraph"}\n'
        "Use only start_block_idx values from the provided headings, in increasing "
        "order.\n\n"
        f"Headings:\n{json.dumps(candidates, ensure_ascii=False)}"
    )


def _fit_section_prompt(chapter_title: str, candidates: list[dict[str, Any]]) -> str:
    """Per-chapter section prompt, bounded to the context like the chapter audit."""
    prompt = _section_audit_prompt(chapter_title, candidates)
    if len(prompt) <= PROMPT_CHAR_BUDGET:
        return prompt
    for cap in (120, 80, 48, 0):
        trimmed = [{**c, "peek": c["peek"][:cap]} for c in candidates]
        prompt = _section_audit_prompt(chapter_title, trimmed)
        if len(prompt) <= PROMPT_CHAR_BUDGET:
            return prompt
    return prompt


def _validate_sections(raw_sections: list, valid_idx: set[int]) -> list[tuple[int, str]]:
    """Keep sections whose start_block_idx is a real in-range heading; sort, dedupe."""
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for s in raw_sections:
        if not isinstance(s, dict):
            continue
        idx = s.get("start_block_idx")
        title = str(s.get("title") or "").strip()
        if not isinstance(idx, int) or idx not in valid_idx or not title or idx in seen:
            continue
        seen.add(idx)
        out.append((idx, title))
    out.sort(key=lambda pair: pair[0])
    return out


def refine_chapter_sections(
    structure: DocumentStructure,
    blocks: list[dict] | None,
    config: dict,
) -> DocumentStructure:
    """Replace each chapter's heuristic sections with a focused per-chapter audit.

    The whole-book hierarchy is too much for a small model, but picking the real
    sections of ONE chapter (only its headings, with level + peek) is a focused
    task it handles well. Per chapter: audit -> set chapter.sections and remap
    that chapter's blocks to the audited section titles. A chapter whose audit
    fails or returns nothing keeps its heuristic sections, so this only ever
    improves on the heuristic.
    """
    if not blocks or not structure.chapters:
        return structure

    chapter_blocks: dict[int, list[int]] = {}
    for idx, ch_num in structure.block_to_chapter.items():
        chapter_blocks.setdefault(ch_num, []).append(idx)

    for chapter in structure.chapters:
        idxs = sorted(chapter_blocks.get(chapter.number, []))
        if not idxs:
            continue
        chapter_start = idxs[0]
        candidates: list[dict[str, Any]] = []
        for idx in idxs:
            block = blocks[idx]
            if block.get("block_type") != "SectionHeader" or idx == chapter_start:
                continue
            text = _clean_heading(block.get("text") or "")
            if not text:
                continue
            candidates.append({
                "block_idx": idx,
                "level": _heading_level(block),
                "text": text[:200],
                "peek": _content_peek(blocks, idx),
            })
        if not candidates:
            continue

        prompt = _fit_section_prompt(chapter.breadcrumb, candidates)
        response = llm.complete(prompt, config, max_tokens=4096, timeout=180.0)
        if not response.strip():
            log.warning("Section audit empty for %s; keeping heuristic sections",
                        chapter.breadcrumb)
            continue
        try:
            payload = _loads_json_object(response)
        except ValueError:
            log.warning("Section audit invalid JSON for %s; keeping heuristic sections",
                        chapter.breadcrumb)
            continue

        valid_idx = {c["block_idx"] for c in candidates}
        sections = _validate_sections(payload.get("sections") or [], valid_idx)
        if not sections:
            continue

        chapter.sections = [
            Section(
                title=title,
                page_start=(blocks[idx].get("page")
                            if isinstance(blocks[idx].get("page"), int) else None),
                parent_chapter=chapter.number,
            )
            for idx, title in sections
        ]
        # Remap this chapter's blocks to the audited section titles only. Blocks
        # before the first section, or under dropped sub-headings, fold into the
        # most recent audited section.
        for idx in idxs:
            structure.block_to_section.pop(idx, None)
        current: str | None = None
        si = 0
        for idx in idxs:
            while si < len(sections) and idx >= sections[si][0]:
                current = sections[si][1]
                si += 1
            if current is not None:
                structure.block_to_section[idx] = current
        log.info("Section audit: %s -> %d sections (from %d candidates)",
                 chapter.breadcrumb, len(sections), len(candidates))

    return structure


def _loads_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("top-level JSON is not an object")
    return value


def _validate_chapters(chapters: list[Any], blocks: list[dict]) -> list[dict[str, Any]]:
    """Keep the well-formed, in-order, unique chapter references; skip the rest.

    Skipping a bad reference (rather than discarding the whole proposal) makes the
    audit robust to the occasional malformed or out-of-order entry — one bad
    chapter no longer forces a fall back to the title-less heuristic structure,
    which is the difference between a stable apply and run-to-run flakiness.
    """
    validated = []
    seen_numbers: set[int] = set()
    seen_starts: set[int] = set()
    previous_start = -1
    previous_number = -1

    for item in chapters:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item["number"])
            start = int(item["start_block_idx"])
        except (KeyError, TypeError, ValueError):
            continue

        if number <= 0 or start < 0 or start >= len(blocks):
            continue
        if number in seen_numbers or start in seen_starts:
            continue
        if start <= previous_start or number <= previous_number:
            continue
        if blocks[start].get("block_type") != "SectionHeader":
            continue

        title = _clean_heading(str(item.get("title") or ""))
        # Empty, or a bare "Chapter N" label: the descriptive title lives in the
        # adjacent heading (some books split the number and the title into two
        # separate headings). Pull the real title from there.
        if not title or re.fullmatch(r"chapter\s+\d+", title, re.IGNORECASE):
            title = _title_from_nearby_heading(blocks, start) or title
        # Strip a leading "Chapter N" label when one heading bundles number+title
        # (e.g. "Chapter 16 Hedge Funds" -> "Hedge Funds").
        title = re.sub(r"^chapter\s+\d+\b[\s:.\-–]*", "", title, flags=re.IGNORECASE).strip() or title

        validated.append({"number": number, "title": title, "start": start})
        seen_numbers.add(number)
        seen_starts.add(start)
        previous_start = start
        previous_number = number

    return validated


def _structure_from_chapters(
    blocks: list[dict],
    title: str,
    chapters: list[dict[str, Any]],
) -> DocumentStructure:
    structure = DocumentStructure(title=title)
    chapter_starts = {ch["start"]: ch for ch in chapters}

    for i, item in enumerate(chapters):
        page_start = blocks[item["start"]].get("page")
        page_end = None
        if i + 1 < len(chapters):
            for block in reversed(blocks[item["start"] + 1 : chapters[i + 1]["start"]]):
                if block.get("page"):
                    page_end = block.get("page")
                    break
        structure.chapters.append(Chapter(
            number=item["number"],
            title=item["title"],
            page_start=page_start if isinstance(page_start, int) else None,
            page_end=page_end if isinstance(page_end, int) else None,
        ))

    current_chapter: int | None = None
    chapter_iter = iter(chapters)
    next_chapter = next(chapter_iter, None)
    for idx in range(len(blocks)):
        while next_chapter and idx >= next_chapter["start"]:
            current_chapter = next_chapter["number"]
            next_chapter = next(chapter_iter, None)
        if current_chapter is not None:
            structure.block_to_chapter[idx] = current_chapter

    skip_titles = {"chapter summary", "notes", "references", "bibliography"}
    section_starts: list[tuple[int, str]] = []
    starts_by_number = _chapter_starts_by_number(chapters)
    for idx, block in enumerate(blocks):
        if block.get("block_type") != "SectionHeader" or idx in chapter_starts:
            continue
        chapter_num = structure.block_to_chapter.get(idx)
        if chapter_num is None:
            continue
        text = _clean_heading(block.get("text") or "")
        if not text or text.lower() in skip_titles:
            continue
        chapter = structure.get_chapter(chapter_num)
        if not chapter:
            continue
        chapter_start = starts_by_number[chapter_num]
        if _same_text(text, chapter.title) and idx - chapter_start <= 2:
            continue
        page = block.get("page")
        chapter.sections.append(Section(
            title=text,
            page_start=page if isinstance(page, int) else None,
            parent_chapter=chapter_num,
        ))
        section_starts.append((idx, text))

    current_section: str | None = None
    section_idx = 0
    current_chapter = None
    for block_idx in range(len(blocks)):
        chapter_num = structure.block_to_chapter.get(block_idx)
        if chapter_num is not None and chapter_num != current_chapter:
            current_chapter = chapter_num
            current_section = None
        while section_idx < len(section_starts) and block_idx >= section_starts[section_idx][0]:
            current_section = section_starts[section_idx][1]
            section_idx += 1
        if current_section:
            structure.block_to_section[block_idx] = current_section

    return structure


def _chapter_starts_by_number(chapters: list[dict[str, Any]]) -> dict[int, int]:
    return {ch["number"]: ch["start"] for ch in chapters}


def _title_from_nearby_heading(blocks: list[dict], start: int) -> str:
    current = _clean_heading(blocks[start].get("text") or "")
    current = re.sub(r"^chapter\s+\S+\s*[:\-–]?\s*", "", current, flags=re.IGNORECASE)
    if current:
        return current
    for block in blocks[start + 1 : start + 4]:
        if block.get("block_type") != "SectionHeader":
            continue
        text = _clean_heading(block.get("text") or "")
        if text:
            return text
    return ""


def _clean_heading(text: str) -> str:
    text = re.sub(r"^#+\s*", "", text).strip()
    text = re.sub(r"\*{1,2}", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _same_text(left: str, right: str) -> bool:
    return re.sub(r"\W+", "", left).lower() == re.sub(r"\W+", "", right).lower()
