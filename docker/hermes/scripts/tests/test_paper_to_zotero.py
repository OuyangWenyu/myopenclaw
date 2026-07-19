"""Tests for paper-to-zotero.py — focus on build_item() field routing logic."""

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

# Stub pyzotero before loading the module under test (not installed locally)
_pyzotero_mock = MagicMock()
sys.modules["pyzotero"] = _pyzotero_mock
sys.modules["pyzotero.zotero"] = _pyzotero_mock.zotero

# Load the module under test ONCE
_MODULE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "paper-to-zotero.py")
)
_spec = importlib.util.spec_from_file_location("paper_to_zotero", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_creators(*names):
    """Build creator list from "Family, Given" pairs."""
    creators = []
    for name in names:
        parts = name.split(", ", 1)
        creators.append({
            "creatorType": "author",
            "lastName": parts[0],
            "firstName": parts[1] if len(parts) > 1 else "",
        })
    return creators


# ── Tests ────────────────────────────────────────────────────────────


class TestJournalArticleRouting:
    """For journalArticle, journal metadata belongs in standard Zotero fields."""

    def test_publication_title_in_item_not_extra(self):
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature Communications"],
            "volume": "17",
            "issue": "1",
            "page": "123-145",
            "ISSN": ["2041-1723"],
            "publisher": "Springer",
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/test", {})

        assert item["publicationTitle"] == "Nature Communications"
        assert "publicationTitle" not in extra

    def test_volume_in_item_not_extra(self):
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature Communications"],
            "volume": "17",
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/test", {})

        assert item["volume"] == "17"
        assert "volume" not in extra

    def test_issue_in_item_not_extra(self):
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature Communications"],
            "issue": "1",
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/test", {})

        assert item["issue"] == "1"
        assert "issue" not in extra

    def test_pages_in_item_not_extra(self):
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature Communications"],
            "page": "123-145",
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/test", {})

        assert item["pages"] == "123-145"
        assert "pages" not in extra

    def test_issn_in_item_not_extra(self):
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature Communications"],
            "ISSN": ["2041-1723"],
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/test", {})

        assert item["ISSN"] == "2041-1723"
        assert "ISSN" not in extra

    def test_publisher_in_item_not_extra(self):
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature Communications"],
            "publisher": "Springer",
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/test", {})

        assert item["publisher"] == "Springer"
        assert "publisher" not in extra

    def test_journal_article_full_routing(self):
        """Integration check: all journal metadata lands in item, none in extra."""
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature Communications"],
            "volume": "17",
            "issue": "1",
            "page": "123-145",
            "ISSN": ["2041-1723"],
            "publisher": "Springer",
            "author": [
                {"given": "John", "family": "Doe"},
                {"given": "Jane", "family": "Smith"},
            ],
            "abstract": "An important result.",
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/test", {})

        # Standard fields
        assert item["itemType"] == "journalArticle"
        assert item["title"] == "Test Paper"
        assert item["publicationTitle"] == "Nature Communications"
        assert item["volume"] == "17"
        assert item["issue"] == "1"
        assert item["pages"] == "123-145"
        assert item["ISSN"] == "2041-1723"
        assert item["publisher"] == "Springer"
        assert item["abstractNote"] == "An important result."
        assert item["date"] == "2026-07-01"
        assert item["DOI"] == "10.1234/test"
        assert item["creators"] == _make_creators("Doe, John", "Smith, Jane")

        # None of the journal fields should be in extra
        journal_keys = {
            "publicationTitle", "volume", "issue", "pages", "ISSN", "publisher"
        }
        assert journal_keys.isdisjoint(set(extra.keys()))


class TestPreprintRouting:
    """For preprint, journal metadata stays in extra."""

    def test_preprint_keeps_metadata_in_extra(self):
        mock_cr = {
            "type": "posted-content",
            "title": ["Preprint Paper"],
            "container-title": ["arXiv preprint"],
            "volume": "1",
            "issue": "2",
            "page": "99-100",
            "ISSN": ["1234-5678"],
            "publisher": "Self-published",
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.48550/arXiv.2501.00001", {})

        assert item["itemType"] == "preprint"
        assert extra["publicationTitle"] == "arXiv preprint"
        assert extra["volume"] == "1"
        assert extra["issue"] == "2"
        assert extra["pages"] == "99-100"
        assert extra["ISSN"] == "1234-5678"
        assert extra["publisher"] == "Self-published"


class TestConferencePaperRouting:
    """For conferencePaper, proceedingsTitle and publisher go to standard fields."""

    def test_proceedings_title_in_item(self):
        mock_cr = {
            "type": "proceedings-article",
            "title": ["Conference Paper"],
            "container-title": ["Proc. of ML Conference 2026"],
            "publisher": "ACM",
            "volume": "1",
            "issue": "2",
            "page": "50-60",
            "ISBN": ["978-1-4503-9999-9"],
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/conf", {})

        assert item["itemType"] == "conferencePaper"
        assert item["proceedingsTitle"] == "Proc. of ML Conference 2026"
        assert item["publisher"] == "ACM"

        # Non-standard fields for conferencePaper stay in extra
        assert extra.get("volume") == "1"
        assert extra.get("issue") == "2"
        assert extra.get("pages") == "50-60"

        # ISBN from Crossref ISBN field
        assert item.get("ISBN") == "978-1-4503-9999-9"
        assert "ISBN" not in extra

    def test_conference_isbn_falls_back_to_issn(self):
        """When ISBN is missing, fall back to ISSN for conference papers."""
        mock_cr = {
            "type": "proceedings-article",
            "title": ["Conference Paper"],
            "container-title": ["Proc. of ML Conference 2026"],
            "publisher": "ACM",
            "ISSN": ["1234-5678"],
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/conf-issn-only", {})

        assert item.get("ISBN") == "1234-5678"


class TestBookSectionRouting:
    """For bookSection, bookTitle and publisher go to standard fields."""

    def test_book_title_in_item(self):
        mock_cr = {
            "type": "book-chapter",
            "title": ["Chapter One"],
            "container-title": ["Advances in Science"],
            "publisher": "Springer",
            "volume": "3",
            "issue": "1",
            "page": "10-25",
            "ISBN": ["978-3-540-99999-9"],
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/bookchap", {})

        assert item["itemType"] == "bookSection"
        assert item["bookTitle"] == "Advances in Science"
        assert item["publisher"] == "Springer"

        # Non-standard fields for bookSection stay in extra
        assert extra.get("volume") == "3"
        assert extra.get("issue") == "1"
        assert extra.get("pages") == "10-25"

        # ISBN from Crossref ISBN field
        assert item.get("ISBN") == "978-3-540-99999-9"
        assert "ISBN" not in extra


class TestBookRouting:
    """For book, publisher goes to standard field, ISBN from Crossref ISBN field."""

    def test_book_publisher_in_item(self):
        mock_cr = {
            "type": "book",
            "title": ["A Great Book"],
            "container-title": [],
            "publisher": "MIT Press",
            "ISBN": ["978-0-262-99999-9"],
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/book", {})

        assert item["itemType"] == "book"
        assert item["publisher"] == "MIT Press"
        assert item.get("ISBN") == "978-0-262-99999-9"

        # No journal metadata should leak into either item or extra
        assert "publicationTitle" not in item
        assert "publicationTitle" not in extra
        assert "volume" not in extra
        assert "issue" not in extra
        assert "pages" not in extra
        assert "ISSN" not in extra


class TestISSNEdgeCases:
    def test_issn_as_string_not_list(self):
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature"],
            "ISSN": "0028-0836",
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/issn-string", {})

        assert item["ISSN"] == "0028-0836"

    def test_issn_empty_list(self):
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature"],
            "ISSN": [],
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/issn-empty", {})

        assert "ISSN" not in item
        assert "ISSN" not in extra


class TestCitationKeyFromStandardFields:
    """After the fix, citationKey should be built from item dict, not extra."""

    def test_citation_key_uses_publication_title_from_item(self):
        mock_cr = {
            "type": "journal-article",
            "title": ["Test Paper"],
            "container-title": ["Nature Communications"],
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/citekey", {})

        # citationKey should be built from item["publicationTitle"]
        assert extra.get("citationKey") == "Doe2026_NC"

    def test_citation_key_uses_proceedings_title(self):
        mock_cr = {
            "type": "proceedings-article",
            "title": ["Conference Paper"],
            "container-title": ["Proc of ML Conference"],
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/citekey-conf", {})

        # citationKey should be built from item["proceedingsTitle"]
        assert extra.get("citationKey") == "Doe2026_PoMC"

    def test_citation_key_uses_book_title(self):
        mock_cr = {
            "type": "book-chapter",
            "title": ["Chapter One"],
            "container-title": ["Advances in Science"],
            "author": [{"given": "John", "family": "Doe"}],
            "published-print": {"date-parts": [[2026, 7, 1]]},
        }

        with patch.object(_mod, "fetch_crossref", return_value=mock_cr):
            item, extra = _mod.build_item("10.1234/citekey-book", {})

        # citationKey should be built from item["bookTitle"]
        assert extra.get("citationKey") == "Doe2026_AiS"
