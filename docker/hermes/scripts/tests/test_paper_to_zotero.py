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

    def test_conference_issn_to_item_field(self):
        """When ISBN is missing, ISSN goes to item.ISSN — NOT item.ISBN."""
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

        assert item.get("ISSN") == "1234-5678"
        assert "ISBN" not in item


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


class TestArxivUpgrade:
    """arXiv DOI → published DOI metadata upgrade (Issue #15)."""

    # ── Shared test data ──────────────────────────────────────────

    ARXIV_DOI = "10.48550/arXiv.2304.12345"
    PUBLISHED_DOI = "10.1000/neurips2023"
    ARXIV_ID = "2304.12345"

    @staticmethod
    def _posted_content_cr():
        return {
            "type": "posted-content",
            "title": ["A Preprint Title"],
            "container-title": ["arXiv preprint"],
            "author": [{"given": "Alice", "family": "Wang"}],
            "published-print": {"date-parts": [[2023, 4, 15]]},
        }

    @staticmethod
    def _published_journal_cr():
        return {
            "type": "journal-article",
            "title": ["Published Title in Journal"],
            "container-title": ["Nature Machine Intelligence"],
            "volume": "5",
            "issue": "6",
            "page": "500-510",
            "ISSN": ["2522-5839"],
            "publisher": "Nature Publishing",
            "author": [{"given": "Alice", "family": "Wang"}],
            "abstract": "Published version abstract.",
            "published-print": {"date-parts": [[2023, 12, 1]]},
        }

    @staticmethod
    def _published_conf_cr():
        return {
            "type": "proceedings-article",
            "title": ["Published Title at Conference"],
            "container-title": ["Advances in Neural Information Processing Systems"],
            "publisher": "Curran Associates",
            "volume": "36",
            "page": "1234-1245",
            "ISBN": ["978-1-7138-7108-8"],
            "author": [{"given": "Alice", "family": "Wang"}],
            "abstract": "Conference version.",
            "published-print": {"date-parts": [[2023, 12, 10]]},
        }

    @staticmethod
    def _arxiv_data_with_published_doi():
        return {
            "title": "A Preprint Title",
            "summary": "Abstract from arXiv.",
            "authors": ["Alice Wang"],
            "published": "2023-04-15",
            "primary_category": "cs.LG",
            "published_doi": "10.1000/neurips2023",
            "journal_ref": "Nature Machine Intelligence 5, 500-510 (2023)",
        }

    @staticmethod
    def _arxiv_data_no_published_doi():
        return {
            "title": "A Preprint Title",
            "summary": "Abstract from arXiv.",
            "authors": ["Alice Wang"],
            "published": "2023-04-15",
            "primary_category": "cs.LG",
            "published_doi": None,
            "journal_ref": None,
        }

    # ── Tests ─────────────────────────────────────────────────────

    def test_arxiv_api_upgrade_to_published_journal(self):
        """arXiv <arxiv:doi> present → upgrade to published journalArticle."""
        posted = self._posted_content_cr()
        published = self._published_journal_cr()
        arxiv_data = self._arxiv_data_with_published_doi()

        def mock_crossref(doi):
            if doi == self.ARXIV_DOI:
                return posted
            if doi == self.PUBLISHED_DOI:
                return published
            return None

        with (
            patch.object(_mod, "fetch_crossref", side_effect=mock_crossref),
            patch.object(_mod, "fetch_arxiv", return_value=arxiv_data) as mock_arxiv,
            patch.object(_mod, "fetch_published_doi_s2") as mock_s2,
        ):
            item, extra = _mod.build_item(self.ARXIV_DOI, {})

        # Upgraded to published metadata
        assert item["itemType"] == "journalArticle"
        assert item["title"] == "Published Title in Journal"
        assert item["publicationTitle"] == "Nature Machine Intelligence"
        assert item["volume"] == "5"
        assert item["issue"] == "6"
        assert item["pages"] == "500-510"
        assert item["ISSN"] == "2522-5839"
        assert item["publisher"] == "Nature Publishing"
        assert item["abstractNote"] == "Published version abstract."
        assert item["date"] == "2023-12-01"

        # arXiv source preserved in extra
        assert extra["repository"] == "arXiv"
        assert extra["archiveID"] == "arXiv:2304.12345"
        assert extra["libraryCatalog"] == "arXiv.org"

        # arXiv API was called, S2 was NOT
        mock_arxiv.assert_called_once_with(self.ARXIV_ID)
        mock_s2.assert_not_called()

    def test_s2_fallback_when_arxiv_doi_missing(self):
        """arXiv <arxiv:doi> absent → S2 fallback → upgrade to conferencePaper."""
        posted = self._posted_content_cr()
        published = self._published_conf_cr()
        arxiv_data = self._arxiv_data_no_published_doi()

        def mock_crossref(doi):
            if doi == self.ARXIV_DOI:
                return posted
            if doi == self.PUBLISHED_DOI:
                return published
            return None

        with (
            patch.object(_mod, "fetch_crossref", side_effect=mock_crossref),
            patch.object(_mod, "fetch_arxiv", return_value=arxiv_data) as mock_arxiv,
            patch.object(
                _mod, "fetch_published_doi_s2", return_value=self.PUBLISHED_DOI
            ) as mock_s2,
        ):
            item, extra = _mod.build_item(self.ARXIV_DOI, {})

        # Upgraded via S2 to conference paper
        assert item["itemType"] == "conferencePaper"
        assert item["title"] == "Published Title at Conference"
        assert item["proceedingsTitle"] == "Advances in Neural Information Processing Systems"
        assert item["publisher"] == "Curran Associates"
        assert item["ISBN"] == "978-1-7138-7108-8"

        # arXiv source preserved
        assert extra["repository"] == "arXiv"
        assert extra["archiveID"] == "arXiv:2304.12345"

        # Both arXiv and S2 were called
        mock_arxiv.assert_called_once_with(self.ARXIV_ID)
        mock_s2.assert_called_once_with(self.ARXIV_ID)

    def test_stays_preprint_when_both_sources_fail(self):
        """arXiv <arxiv:doi> absent AND S2 returns None → preprint metadata."""
        posted = self._posted_content_cr()
        arxiv_data = self._arxiv_data_no_published_doi()

        def mock_crossref(doi):
            if doi == self.ARXIV_DOI:
                return posted
            return None

        with (
            patch.object(_mod, "fetch_crossref", side_effect=mock_crossref),
            patch.object(_mod, "fetch_arxiv", return_value=arxiv_data),
            patch.object(_mod, "fetch_published_doi_s2", return_value=None) as mock_s2,
        ):
            item, extra = _mod.build_item(self.ARXIV_DOI, {})

        # Falls through to preprint (posted-content not in type_map → preprint)
        assert item["itemType"] == "preprint"
        assert item["title"] == "A Preprint Title"

        # Container info goes to extra (preprint routing)
        assert extra["publicationTitle"] == "arXiv preprint"

        # arXiv source marked
        assert extra["repository"] == "arXiv"
        assert extra["archiveID"] == "arXiv:2304.12345"

        # S2 was attempted
        mock_s2.assert_called_once_with(self.ARXIV_ID)

    def test_arxiv_source_preserved_after_upgrade(self):
        """After metadata upgrade, arXiv source fields are present in extra."""
        posted = self._posted_content_cr()
        published = self._published_journal_cr()
        arxiv_data = self._arxiv_data_with_published_doi()

        def mock_crossref(doi):
            if doi == self.ARXIV_DOI:
                return posted
            if doi == self.PUBLISHED_DOI:
                return published
            return None

        with (
            patch.object(_mod, "fetch_crossref", side_effect=mock_crossref),
            patch.object(_mod, "fetch_arxiv", return_value=arxiv_data),
            patch.object(_mod, "fetch_published_doi_s2"),
        ):
            item, extra = _mod.build_item(self.ARXIV_DOI, {})

        # Source fields are present (added after upgrade)
        assert extra["repository"] == "arXiv"
        assert extra["archiveID"] == "arXiv:2304.12345"
        assert extra["libraryCatalog"] == "arXiv.org"

    def test_non_arxiv_doi_skips_upgrade_path(self):
        """Regular DOI (not arXiv) — no arXiv/S2 calls, normal routing."""
        journal_cr = self._published_journal_cr()

        with (
            patch.object(_mod, "fetch_crossref", return_value=journal_cr) as mock_cr,
            patch.object(_mod, "fetch_arxiv") as mock_arxiv,
            patch.object(_mod, "fetch_published_doi_s2") as mock_s2,
        ):
            item, extra = _mod.build_item(self.PUBLISHED_DOI, {})

        # Normal journalArticle routing
        assert item["itemType"] == "journalArticle"
        assert item["title"] == "Published Title in Journal"
        assert item["publicationTitle"] == "Nature Machine Intelligence"

        # arXiv and S2 were NEVER called
        mock_arxiv.assert_not_called()
        mock_s2.assert_not_called()

        # Crossref called exactly once with the published DOI
        mock_cr.assert_called_once_with(self.PUBLISHED_DOI)
