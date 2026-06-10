"""Tests for LLM-backed, taxonomy-aware tagging."""

import librarian.classify as classify


class TestParseTagObject:
    def test_clean_object(self):
        subjects, library = classify._parse_tag_object(
            '{"subjects": ["finance/cycle-analysis", "finance/trading"], "library": "trading"}'
        )
        assert subjects == ["finance/cycle-analysis", "finance/trading"]
        assert library == "trading"

    def test_object_with_surrounding_text(self):
        # Models sometimes wrap the JSON in prose despite instructions.
        subjects, library = classify._parse_tag_object(
            'Here are the tags:\n{"subjects": ["a/b"], "library": "x"}\nHope that helps.'
        )
        assert subjects == ["a/b"]
        assert library == "x"

    def test_blank_library_becomes_none(self):
        subjects, library = classify._parse_tag_object(
            '{"subjects": ["a/b"], "library": "  "}'
        )
        assert subjects == ["a/b"]
        assert library is None

    def test_malformed_returns_empty(self):
        assert classify._parse_tag_object("not json at all") == ([], None)
        assert classify._parse_tag_object('{"subjects": [oops]}') == ([], None)

    def test_non_string_subjects_filtered(self):
        subjects, _ = classify._parse_tag_object(
            '{"subjects": ["a/b", 5, "", "c/d"], "library": "x"}'
        )
        assert subjects == ["a/b", "c/d"]


class TestSuggestSubjectsLlm:
    def test_uses_llm_and_passes_taxonomy(self, monkeypatch):
        captured = {}

        def fake_complete(prompt, config, max_tokens=1024, timeout=120.0):
            captured["prompt"] = prompt
            return '{"subjects": ["finance/cycle-analysis"], "library": "trading"}'

        monkeypatch.setattr(classify, "complete", fake_complete, raising=False)
        # complete is imported inside the function; patch the source module too
        import librarian.llm as llm
        monkeypatch.setattr(llm, "complete", fake_complete)

        subjects, library = classify.suggest_subjects_llm(
            title="The Power of Oscillator-Cycle Combinations",
            authors=["Walter Bressert"],
            content_sample="detrended price oscillator, market cycles, timing bands",
            subjects_taxonomy=["finance/trading", "mathematics/dynamical-systems"],
            libraries_taxonomy=["money-reading-list", "mathematics"],
            config={"classification": {"provider": "vllm"}},
        )

        assert subjects == ["finance/cycle-analysis"]
        assert library == "trading"
        # taxonomy is in the prompt as guidance, and new-tag creation is encouraged
        assert "finance/trading" in captured["prompt"]
        assert "NEW" in captured["prompt"] or "new" in captured["prompt"]

    def test_empty_llm_response_returns_empty(self, monkeypatch):
        import librarian.llm as llm
        monkeypatch.setattr(llm, "complete", lambda *a, **k: "")

        subjects, library = classify.suggest_subjects_llm(
            "t", [], "sample", [], [], {"classification": {}}
        )
        assert subjects == []
        assert library is None
