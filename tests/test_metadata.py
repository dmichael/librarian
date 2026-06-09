"""Tests for external-metadata helpers (name normalization, ISBN lookup parsing)."""

import httpx
import pytest

from librarian import metadata


class TestNormalizeAuthorName:
    @pytest.mark.parametrize("a,b", [
        ("Robert Pozen", "Pozen, Robert"),
        ("J. M. Selig", "J.M. Selig"),
        ("J. M. Selig", "j m selig"),
        ("  Jane   Doe ", "jane doe"),
    ])
    def test_equivalent_forms_normalize_identically(self, a, b):
        assert metadata.normalize_author_name(a) == metadata.normalize_author_name(b)

    def test_different_names_stay_different(self):
        assert (
            metadata.normalize_author_name("Jane Doe")
            != metadata.normalize_author_name("John Doe")
        )


class TestCompareAuthors:
    def test_match_across_formats(self):
        assert metadata.compare_authors(["Pozen, Robert"], ["Robert Pozen"])

    def test_both_empty_is_match(self):
        assert metadata.compare_authors([], [])

    def test_one_empty_is_mismatch(self):
        assert not metadata.compare_authors(["A. Author"], [])

    def test_different_authors_is_mismatch(self):
        assert not metadata.compare_authors(["Jane Doe"], ["John Smith"])


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class TestLookupIsbn:
    def test_google_books_parsing(self, monkeypatch):
        payload = {
            "totalItems": 1,
            "items": [{
                "volumeInfo": {
                    "title": "The Fund Industry",
                    "authors": ["Robert Pozen", "Theresa Hamacher"],
                    "publisher": "Wiley",
                }
            }],
        }
        monkeypatch.setattr(httpx, "get", lambda url, timeout: _FakeResponse(payload))

        result = metadata.lookup_isbn_google("978-1-118-92994-0")

        assert result is not None
        assert result.title == "The Fund Industry"
        assert result.authors == ["Robert Pozen", "Theresa Hamacher"]
        assert result.isbn == "9781118929940"  # dashes stripped
        assert result.source == "google_books"

    def test_google_books_no_results(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "get", lambda url, timeout: _FakeResponse({"totalItems": 0})
        )
        assert metadata.lookup_isbn_google("0000000000") is None

    def test_lookup_falls_back_to_openlibrary(self, monkeypatch):
        def fake_get(url, timeout):
            if "googleapis" in url:
                return _FakeResponse({"totalItems": 0})
            return _FakeResponse({
                "ISBN:9781118929940": {
                    "title": "The Fund Industry",
                    "authors": [{"name": "Robert Pozen"}],
                    "publishers": [{"name": "Wiley"}],
                }
            })

        monkeypatch.setattr(httpx, "get", fake_get)

        result = metadata.lookup_isbn("9781118929940")

        assert result is not None
        assert result.source == "openlibrary"
        assert result.authors == ["Robert Pozen"]
        assert result.publisher == "Wiley"

    def test_network_error_returns_none(self, monkeypatch):
        def fail(url, timeout):
            raise httpx.RequestError("down")

        monkeypatch.setattr(httpx, "get", fail)
        assert metadata.lookup_isbn("9781118929940") is None
