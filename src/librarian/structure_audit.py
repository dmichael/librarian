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

    prompt = _build_prompt(title, headings, structure)
    response = llm.complete(prompt, config, max_tokens=4096, timeout=180.0)

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
    return StructureAuditResult(repaired, True, f"applied {len(validated)} chapters"), reasoning


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
            "text": text[:240],
            "peek": _content_peek(blocks, idx),
        })
    return records


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
        '"reasoning":"one short paragraph"}\n\n'
        "Rules:\n"
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
    validated = []
    seen_numbers: set[int] = set()
    seen_starts: set[int] = set()
    previous_start = -1
    previous_number = -1

    for item in chapters:
        if not isinstance(item, dict):
            return []
        try:
            number = int(item["number"])
            start = int(item["start_block_idx"])
        except (KeyError, TypeError, ValueError):
            return []

        if number <= 0 or start < 0 or start >= len(blocks):
            return []
        if number in seen_numbers or start in seen_starts:
            return []
        if start <= previous_start or number <= previous_number:
            return []
        if blocks[start].get("block_type") != "SectionHeader":
            return []

        title = _clean_heading(str(item.get("title") or ""))
        if not title:
            title = _title_from_nearby_heading(blocks, start)

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
