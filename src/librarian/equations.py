"""Equation extraction and indexing for mathematical content.

This module provides:
1. Extraction of LaTeX equations from markdown with surrounding context
2. Structured representation of equations with metadata
3. Equation-aware text chunking that keeps equations with their explanations

The dual-indexing strategy stores equations both:
- In their natural context (as part of text chunks)
- As standalone searchable entities (equation collection)

This enables queries like:
- "oscillator differential equation" → finds equation by description
- "what equations model birdsong" → finds equations by context
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pylatexenc.latex2text import LatexNodes2Text

from librarian.htmltext import html_to_text

_latex_converter = LatexNodes2Text()


@dataclass
class ExtractedEquation:
    """A mathematical equation extracted from a document."""

    latex: str  # Raw LaTeX (without $$ delimiters)
    description: str  # Natural language description from pylatexenc
    context_before: str  # Text preceding the equation
    context_after: str  # Text following the equation
    equation_number: str | None  # If labeled, e.g., "(4)" or "Eq. 3"

    # Source metadata (populated during indexing)
    book_id: int | None = None
    title: str = ""
    page: int | None = None
    position: int = 0  # Character offset in source document

    # Derived fields
    context_window: str = field(init=False)  # Combined context for embedding
    searchable_text: str = field(init=False)  # Text optimized for retrieval

    def __post_init__(self):
        # Combine context for embedding
        self.context_window = f"{self.context_before}\n$${self.latex}$$\n{self.context_after}"

        # Build searchable text: description + equation + context keywords
        parts = [self.description]
        if self.equation_number:
            parts.append(f"equation {self.equation_number}")

        # Extract key terms from context (the "where X is..." part)
        where_match = re.search(
            r"where\s+(.+?)(?:\.|$)",
            self.context_after,
            re.IGNORECASE | re.DOTALL,
        )
        if where_match:
            parts.append(where_match.group(1))

        self.searchable_text = " ".join(parts)


def extract_equations_from_blocks(
    blocks: list[dict], context_chars: int = 500
) -> list[ExtractedEquation]:
    """Extract equations from marker JSON blocks.

    Handles both raw blocks (with HTML/MathML) and processed blocks
    (with text field from load_extracted_blocks).

    Args:
        blocks: List of block dicts from marker JSON output
        context_chars: Characters of context to capture from adjacent blocks

    Returns:
        List of ExtractedEquation objects with context
    """
    equations = []

    from librarian.extractors.marker import parse_equation_html

    for i, block in enumerate(blocks):
        if str(block.get("block_type", "")).lower() != "equation":
            continue

        # Try to get LaTeX from different sources
        latex = None
        eq_num = None

        # Option 1: Raw HTML with MathML — marker.py owns the parsing quirks
        # (latex normalization and the three equation-number conventions).
        html = block.get("html", "")
        if html:
            parsed = parse_equation_html(html)
            if parsed:
                latex, eq_num = parsed

        # Option 2: Processed text field (from load_extracted_blocks)
        if not latex:
            text = block.get("text", "")
            if text:
                # Clean up escaped underscores from markdown conversion
                latex = text.replace(r"\_", "_").strip()

        if not latex:
            continue

        # Get page number
        page = block.get("page")

        # Gather context from surrounding blocks
        context_before_parts = []
        chars_collected = 0
        for j in range(i - 1, -1, -1):
            prev_block = blocks[j]
            if prev_block.get("block_type") == "Equation":
                continue  # Skip other equations
            text = prev_block.get("text", "")
            if not text:
                # Try to extract from HTML
                text = html_to_text(prev_block.get("html", ""), "flat")
            if text:
                context_before_parts.insert(0, text)
                chars_collected += len(text)
                if chars_collected >= context_chars:
                    break

        context_after_parts = []
        chars_collected = 0
        for j in range(i + 1, len(blocks)):
            next_block = blocks[j]
            if next_block.get("block_type") == "Equation":
                continue
            text = next_block.get("text", "")
            if not text:
                text = html_to_text(next_block.get("html", ""), "flat")
            if text:
                context_after_parts.append(text)
                chars_collected += len(text)
                if chars_collected >= context_chars:
                    break

        context_before = " ".join(context_before_parts)[-context_chars:]
        context_after = " ".join(context_after_parts)[:context_chars]

        # Fallback when the HTML parse found no number: look for one in the
        # LaTeX (text path) or nearby trailing context.
        if eq_num is None:
            num_patterns = [
                r"\((\d+)\)",  # (4)
                r"\\tag\{(\d+)\}",  # \tag{4}
                r"[Ee]q(?:uation)?\.?\s*(\d+)",  # Eq. 4
            ]
            for pat in num_patterns:
                num_match = re.search(pat, latex + " " + context_after[:100])
                if num_match:
                    eq_num = num_match.group(1)
                    break

        # Generate natural language description
        try:
            description = _latex_converter.latex_to_text(latex)
            description = re.sub(r"\s+", " ", description).strip()
        except Exception:
            description = ""

        eq = ExtractedEquation(
            latex=latex,
            description=description,
            context_before=context_before,
            context_after=context_after,
            equation_number=eq_num,
            page=page,
            position=i,  # Block index as position
        )
        equations.append(eq)

    return equations


def extract_equations(text: str, context_chars: int = 500) -> list[ExtractedEquation]:
    """Extract all display equations from markdown text.

    Args:
        text: Markdown text containing LaTeX equations
        context_chars: Characters of context to capture before/after equation

    Returns:
        List of ExtractedEquation objects with context
    """
    equations = []

    # Match display equations $$...$$
    pattern = r"\$\$(.+?)\$\$"

    for match in re.finditer(pattern, text, flags=re.DOTALL):
        latex = match.group(1).strip()
        start, end = match.span()

        # Extract surrounding context
        context_start = max(0, start - context_chars)
        context_end = min(len(text), end + context_chars)

        context_before = text[context_start:start].strip()
        context_after = text[end:context_end].strip()

        # Try to find equation number in nearby text
        # Patterns: "(4)", "Eq. 4", "equation 4", or just a number after $$
        eq_num = None
        num_patterns = [
            r"\((\d+)\)",  # (4)
            r"[Ee]q(?:uation)?\.?\s*(\d+)",  # Eq. 4, equation 4
            r"^\s*(\d+)\s*$",  # Standalone number after equation
        ]
        for pat in num_patterns:
            # Check in the 50 chars after equation
            after_snippet = text[end : end + 50]
            num_match = re.search(pat, after_snippet)
            if num_match:
                eq_num = num_match.group(1)
                break

        # Generate natural language description
        try:
            description = _latex_converter.latex_to_text(latex)
            description = re.sub(r"\s+", " ", description).strip()
        except Exception:
            description = ""

        equations.append(
            ExtractedEquation(
                latex=latex,
                description=description,
                context_before=context_before,
                context_after=context_after,
                equation_number=eq_num,
                position=start,
            )
        )

    return equations


def classify_equation(eq: ExtractedEquation) -> list[str]:
    """Classify equation type based on content and context.

    Returns list of tags like: ["differential", "oscillator", "physics"]
    """
    tags = []
    combined = f"{eq.latex} {eq.context_before} {eq.context_after}".lower()

    # Equation type detection
    if r"\frac{d" in eq.latex or r"\dot" in eq.latex or r"\ddot" in eq.latex:
        tags.append("differential")
    if "=" in eq.latex:
        tags.append("equality")
    if r"\int" in eq.latex:
        tags.append("integral")
    if r"\sum" in eq.latex:
        tags.append("summation")
    if r"\partial" in eq.latex:
        tags.append("partial-differential")
    if re.search(r"\\(sin|cos|tan|exp|log)", eq.latex):
        tags.append("transcendental")
    if r"\vec" in eq.latex or r"\mathbf" in eq.latex:
        tags.append("vector")
    if r"\matrix" in eq.latex or r"\begin{pmatrix}" in eq.latex:
        tags.append("matrix")

    # Domain detection from context
    domain_keywords = {
        "physics": ["force", "mass", "energy", "momentum", "pressure", "velocity"],
        "biology": ["population", "growth", "decay", "species", "cell"],
        "acoustics": ["sound", "frequency", "oscillat", "vibrat", "resonan"],
        "neuroscience": ["neuron", "synap", "firing", "membrane", "potential"],
        "economics": ["price", "demand", "supply", "utility", "equilibrium"],
        "statistics": ["probability", "distribution", "variance", "mean", "expect"],
    }

    for domain, keywords in domain_keywords.items():
        if any(kw in combined for kw in keywords):
            tags.append(domain)

    return tags


class EquationAwareChunker:
    """Text chunker that respects equation boundaries.

    Ensures equations stay with their surrounding explanation,
    particularly the "where X is Y" definitions that follow equations.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        equation_context_chars: int = 300,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.equation_context_chars = equation_context_chars

    def chunk(self, text: str) -> list[dict]:
        """Split text into chunks, keeping equations with context.

        Returns list of dicts with 'text' and 'equations' keys.
        """
        # First, identify equation zones (equation + surrounding context)
        equation_zones = self._find_equation_zones(text)

        # Split text at safe boundaries (outside equation zones)
        chunks = []
        current_pos = 0

        while current_pos < len(text):
            # Determine chunk end
            chunk_end = min(current_pos + self.chunk_size, len(text))

            # Check if we're cutting through an equation zone
            for zone_start, zone_end in equation_zones:
                if current_pos < zone_start < chunk_end < zone_end:
                    # We'd cut the equation zone - extend to include it
                    chunk_end = zone_end
                    break
                elif zone_start <= current_pos < zone_end:
                    # We're starting inside a zone - include all of it
                    chunk_end = max(chunk_end, zone_end)
                    break

            # Find a clean break point (sentence end, paragraph)
            chunk_end = self._find_break_point(text, chunk_end, current_pos)

            chunk_text = text[current_pos:chunk_end].strip()
            if chunk_text:
                # Extract equations in this chunk
                chunk_equations = extract_equations(chunk_text)
                chunks.append({
                    "text": chunk_text,
                    "equations": chunk_equations,
                    "start": current_pos,
                    "end": chunk_end,
                })

            # Move to next chunk with overlap
            current_pos = chunk_end - self.chunk_overlap
            if current_pos <= chunks[-1]["start"] if chunks else 0:
                current_pos = chunk_end  # Avoid infinite loop

        return chunks

    def _find_equation_zones(self, text: str) -> list[tuple[int, int]]:
        """Find regions around equations that should stay together."""
        zones = []
        pattern = r"\$\$(.+?)\$\$"

        for match in re.finditer(pattern, text, flags=re.DOTALL):
            eq_start, eq_end = match.span()

            # Extend zone to include context
            zone_start = max(0, eq_start - self.equation_context_chars)
            zone_end = min(len(text), eq_end + self.equation_context_chars)

            # Extend zone_end to capture "where X is..." definitions
            where_match = re.search(
                r"where\s+.+?(?:\.\s|\n\n|$)",
                text[eq_end : eq_end + 500],
                re.IGNORECASE | re.DOTALL,
            )
            if where_match:
                zone_end = eq_end + where_match.end()

            zones.append((zone_start, zone_end))

        # Merge overlapping zones
        if not zones:
            return []

        zones.sort()
        merged = [zones[0]]
        for start, end in zones[1:]:
            if start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        return merged

    def _find_break_point(self, text: str, target: int, min_pos: int) -> int:
        """Find a clean break point near target position."""
        # Look for paragraph break first
        para_break = text.rfind("\n\n", min_pos, target + 50)
        if para_break > min_pos + self.chunk_size // 2:
            return para_break + 2

        # Look for sentence end
        for punct in [". ", ".\n", "? ", "! "]:
            sent_break = text.rfind(punct, min_pos, target + 50)
            if sent_break > min_pos + self.chunk_size // 2:
                return sent_break + len(punct)

        return target


def prepare_equation_documents(
    equations: list[ExtractedEquation],
    book_metadata: dict,
) -> list[dict]:
    """Prepare equations for indexing as standalone documents.

    Args:
        equations: Extracted equations from a book
        book_metadata: Book metadata for the source book

    Returns:
        List of document dicts ready for vector store insertion
    """
    documents = []

    for i, eq in enumerate(equations):
        # Classify the equation
        tags = classify_equation(eq)

        doc = {
            # Primary content for embedding
            "text": eq.searchable_text,
            # Metadata for filtering and display
            # Lists are serialized to JSON for LanceDB compatibility
            "metadata": {
                "type": "equation",  # Distinguishes from text chunks
                "latex": eq.latex,
                "description": eq.description,
                "equation_number": eq.equation_number,
                "context_window": eq.context_window,
                "tags": json.dumps(tags),
                "book_id": book_metadata.get("id"),
                "title": book_metadata.get("title", ""),
                "authors": ", ".join(book_metadata.get("authors", [])),
                "position": eq.position,
            },
        }
        documents.append(doc)

    return documents
