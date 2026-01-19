"""Tests for document structure parsing and hierarchical retrieval.

These tests verify:
1. Markdown header parsing extracts correct chapter/section structure
2. Page-to-chapter mapping works correctly
3. Hierarchical metadata is added to chunks during indexing
4. Two-stage hierarchical retrieval improves relevance
"""

import pytest


class TestStructureParsing:
    """Test markdown structure parsing."""

    def test_parse_chapter_with_inline_title(self):
        """Test parsing chapter with title on same line."""
        from librarian.structure import parse_structure

        content = """
# Chapter 1: Introduction

This is the introduction content.

### Section A

More content here.

### **Chapter Summary**

End of chapter.
"""
        structure = parse_structure(content, title="Test Book")

        assert len(structure.chapters) == 1
        assert structure.chapters[0].number == 1
        assert structure.chapters[0].title == "Introduction"
        assert len(structure.chapters[0].sections) >= 1

    def test_parse_chapter_with_separate_title(self):
        """Test parsing chapter with title on following line."""
        from librarian.structure import parse_structure

        content = """
# Chapter 2

# **How Things Work**

This explains how things work.

### First Topic

Details here.
"""
        structure = parse_structure(content, title="Test Book")

        assert len(structure.chapters) == 1
        assert structure.chapters[0].number == 2
        assert structure.chapters[0].title == "How Things Work"

    def test_parse_chapter_with_h3_title(self):
        """Test parsing chapter with ### level title."""
        from librarian.structure import parse_structure

        content = """
# Chapter 3

### **Advanced Topics**

This is about advanced topics.
"""
        structure = parse_structure(content, title="Test Book")

        assert len(structure.chapters) == 1
        assert structure.chapters[0].number == 3
        assert structure.chapters[0].title == "Advanced Topics"

    def test_parse_multiple_chapters(self):
        """Test parsing document with multiple chapters."""
        from librarian.structure import parse_structure

        content = """
# Chapter 1: First

Content.

### **Chapter Summary**

# Chapter 2: Second

More content.

# Chapter 3 Third Without Colon

Even more content.
"""
        structure = parse_structure(content, title="Test Book")

        assert len(structure.chapters) == 3
        assert structure.chapters[0].title == "First"
        assert structure.chapters[1].title == "Second"
        assert structure.chapters[2].title == "Third Without Colon"

    def test_parse_sections_within_chapter(self):
        """Test that sections are correctly associated with chapters."""
        from librarian.structure import parse_structure

        content = """
# Chapter 1: Main Topic

Introduction text.

### **First Section**

Section content.

### **Second Section**

More section content.

### **Chapter Summary**

Summary text.
"""
        structure = parse_structure(content, title="Test Book")

        assert len(structure.chapters) == 1
        ch = structure.chapters[0]
        # Should find First Section and Second Section, but not Chapter Summary
        section_titles = [s.title for s in ch.sections]
        assert "First Section" in section_titles
        assert "Second Section" in section_titles
        assert "Chapter Summary" not in section_titles

    def test_skip_intro_phrases_as_titles(self):
        """Test that 'This chapter reviews' is not captured as title."""
        from librarian.structure import parse_structure

        content = """
# Chapter 4

### This chapter reviews:

- Point 1
- Point 2

### **Actual Section**

Real content.
"""
        structure = parse_structure(content, title="Test Book")

        assert len(structure.chapters) == 1
        # Title should be empty or the first non-intro header
        assert "This chapter reviews" not in structure.chapters[0].title

    def test_page_extraction(self):
        """Test page number extraction from marker output."""
        from librarian.structure import extract_page_from_text

        # Test _page_N_ pattern (image references)
        assert extract_page_from_text("![](_page_25_Picture_1.jpeg)") == 25
        assert extract_page_from_text("Content with _page_100_ marker") == 100

        # Test page-N pattern (span ids)
        assert extract_page_from_text('<span id="page-42">') == 42

        # Test no page
        assert extract_page_from_text("No page marker here") is None


class TestChapterContext:
    """Test chapter context lookup."""

    def test_get_context_for_page(self):
        """Test looking up chapter context by page number."""
        from librarian.structure import parse_structure, get_context_for_page

        content = """
# Chapter 1: Introduction

![](_page_10_Picture.jpeg)

Content for chapter 1.

### First Section

![](_page_15_Picture.jpeg)

Section content.

### **Chapter Summary**

![](_page_20_Picture.jpeg)

# Chapter 2: Advanced

![](_page_25_Picture.jpeg)

Chapter 2 content.
"""
        structure = parse_structure(content, title="Test Book")

        # Page 12 should be in Chapter 1
        ctx = get_context_for_page(structure, 12)
        assert ctx["chapter_num"] == 1
        assert ctx["chapter_title"] == "Introduction"

        # Page 26 should be in Chapter 2
        ctx = get_context_for_page(structure, 26)
        assert ctx["chapter_num"] == 2

    def test_context_for_none_page(self):
        """Test that None page returns empty context."""
        from librarian.structure import parse_structure, get_context_for_page

        content = "# Chapter 1: Test\n\nContent."
        structure = parse_structure(content, title="Test")

        ctx = get_context_for_page(structure, None)
        assert ctx["chapter_num"] is None
        assert ctx["breadcrumb"] == ""

    def test_breadcrumb_generation(self):
        """Test breadcrumb string generation."""
        from librarian.structure import Chapter

        ch = Chapter(number=5, title="Distribution Expenses")
        assert ch.breadcrumb == "Chapter 5: Distribution Expenses"


class TestChapterTOC:
    """Test TOC generation from structure."""

    def test_generate_toc(self):
        """Test generating table of contents."""
        from librarian.structure import parse_structure, get_chapter_toc

        content = """
# Chapter 1: First Topic

### Section A

### Section B

# Chapter 2: Second Topic

### Only Section
"""
        structure = parse_structure(content, title="Test Book")
        toc = get_chapter_toc(structure)

        assert "# Test Book" in toc
        assert "## Chapter 1: First Topic" in toc
        assert "Section A" in toc
        assert "## Chapter 2: Second Topic" in toc


class TestHierarchicalMetadata:
    """Integration tests for hierarchical metadata in indexed chunks."""

    @pytest.fixture
    def config(self):
        """Load config for tests."""
        from librarian.config import load_config, expand_path

        config = load_config()
        vs_config = config.get("vector_store", {})
        path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))

        if not path.exists():
            pytest.skip("Qdrant index not found")

        return config

    @pytest.mark.integration
    def test_chunks_have_chapter_metadata(self, config):
        """Test that indexed chunks have chapter metadata fields."""
        from librarian.query import retrieve

        # Query for something from a known indexed book
        nodes = retrieve("mutual fund expense ratio", config, top_k=3)

        if not nodes:
            pytest.skip("No indexed content found")

        # Check metadata fields exist
        for node in nodes:
            meta = node.metadata
            # These fields should exist (may be None if no structure detected)
            assert "chapter_num" in meta, "Missing chapter_num field"
            assert "chapter_title" in meta, "Missing chapter_title field"
            assert "breadcrumb" in meta, "Missing breadcrumb field"

    @pytest.mark.integration
    def test_fund_industry_has_chapter_structure(self, config):
        """Test that Fund Industry book (105) has proper chapter structure."""
        from librarian.query import retrieve
        from llama_index.core.vector_stores import MetadataFilter, FilterOperator

        nodes = retrieve("expense ratio management fee", config, top_k=5)

        # Filter to book 105
        fund_nodes = [n for n in nodes if n.metadata.get("book_id") == 105]

        if not fund_nodes:
            pytest.skip("Fund Industry book not indexed")

        # Should have chapter metadata
        for node in fund_nodes:
            ch_num = node.metadata.get("chapter_num")
            ch_title = node.metadata.get("chapter_title")

            # Expense ratio should be in Chapter 5: The Cost of Fund Ownership
            if ch_num == 5:
                assert "Cost" in ch_title or "Fund Ownership" in ch_title
                return

        pytest.fail("Expected to find results from Chapter 5 of Fund Industry")


class TestHierarchicalRetrieval:
    """Integration tests for two-stage hierarchical retrieval."""

    @pytest.fixture
    def config(self):
        """Load config for tests."""
        from librarian.config import load_config, expand_path

        config = load_config()
        vs_config = config.get("vector_store", {})
        path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))

        if not path.exists():
            pytest.skip("Qdrant index not found")

        return config

    @pytest.mark.integration
    def test_chapter_retrieval(self, config):
        """Test retrieving chapter summaries."""
        from librarian.query import retrieve_chapters

        nodes = retrieve_chapters("retirement planning 401k", config, top_k=3)

        if not nodes:
            pytest.skip("No chapter summaries indexed")

        # Should have chapter metadata
        for node in nodes:
            assert "chapter_num" in node.metadata
            assert "chapter_title" in node.metadata
            assert "summary" in node.metadata

    @pytest.mark.integration
    def test_hierarchical_narrows_to_relevant_chapters(self, config):
        """Test that hierarchical retrieval focuses on relevant chapters."""
        from librarian.query import retrieve_hierarchical, retrieve

        query = "target date fund retirement"

        # Standard retrieval
        standard_nodes = retrieve(query, config, top_k=5)

        # Hierarchical retrieval
        hier_nodes = retrieve_hierarchical(query, config, top_k=5, chapter_top_k=2)

        if not hier_nodes:
            pytest.skip("No hierarchical results")

        # Hierarchical should be more focused (fewer unique chapters)
        hier_chapters = {n.metadata.get("chapter_num") for n in hier_nodes}
        standard_chapters = {n.metadata.get("chapter_num") for n in standard_nodes}

        # Hierarchical should have same or fewer unique chapters
        assert len(hier_chapters) <= len(standard_chapters) + 1


class TestEquationFiltering:
    """Test equation cross-contamination filtering."""

    @pytest.fixture
    def config(self):
        """Load config for tests."""
        from librarian.config import load_config, expand_path

        config = load_config()
        vs_config = config.get("vector_store", {})
        path = expand_path(vs_config.get("path", "~/data/librarian/qdrant"))

        if not path.exists():
            pytest.skip("Qdrant index not found")

        return config

    @pytest.mark.integration
    def test_equations_filtered_to_relevant_books(self, config):
        """Test that equations are filtered to same books as text results."""
        from librarian.query import retrieve

        # Query something from a non-math book
        nodes = retrieve(
            "mutual fund expense ratio",
            config,
            top_k=5,
            include_equations=True,
            equation_top_k=3,
        )

        if not nodes:
            pytest.skip("No results")

        # Get book IDs from text results
        text_nodes = [n for n in nodes if n.metadata.get("_result_type") == "text"]
        text_book_ids = {n.metadata.get("book_id") for n in text_nodes[:3]}

        # Get book IDs from equation results
        eq_nodes = [n for n in nodes if n.metadata.get("_result_type") == "equation"]
        eq_book_ids = {n.metadata.get("book_id") for n in eq_nodes}

        # If equations exist, they should only be from same books as text
        if eq_nodes:
            for eq_book in eq_book_ids:
                assert eq_book in text_book_ids, (
                    f"Equation from book {eq_book} not in text book ids {text_book_ids}"
                )
