"""
Tests for vocabulary.py: vocabularies, entries, lookups and hierarchical trees.
"""

from urllib.parse import parse_qsl, urlparse

import pytest
import responses

from dspace_api.exceptions import DSpaceError
from dspace_api.vocabulary import CONFIDENCE_ACCEPTED, VocabularyManager, detail_id, has_children

COLL = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture
def mgr(http_auth):
    return VocabularyManager(http_auth)


def params(http, index=-1):
    return parse_qsl(urlparse(http.calls[index].request.url).query)


def detail(ident, children=None, **extra):
    info = {} if children is None else {"hasChildren": "true" if children else "false"}
    return {"id": ident, "value": ident.split(":")[1], "otherInformation": info, **extra}


class TestHelpers:
    def test_detail_id(self):
        assert detail_id("srsc", "SCB110") == "srsc:SCB110"
        assert detail_id("srsc", "srsc:SCB110") == "srsc:SCB110"
        assert detail_id("srsc", 7) == "srsc:7"

    def test_has_children(self):
        assert has_children({"otherInformation": {"hasChildren": "true"}}) is True
        assert has_children({"otherInformation": {"hasChildren": False}}) is False
        assert has_children({}) is None

    def test_to_metadata_value(self):
        assert VocabularyManager.to_metadata_value({"value": "Article", "display": "Artikel"}) == {"value": "Article"}
        assert VocabularyManager.to_metadata_value({"display": "Only display"}, language="en") == {
            "value": "Only display", "language": "en"}
        assert VocabularyManager.to_metadata_value({"value": "Doe", "authority": "a-1"}) == {
            "value": "Doe", "authority": "a-1", "confidence": CONFIDENCE_ACCEPTED}


class TestVocabularies:
    def test_list_and_get(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/submission/vocabularies"),
                 json=hal_page("vocabularies", [{"id": "srsc"}, {"id": "common_types"}]))
        http.add(responses.GET, api_url("/submission/vocabularies/srsc"), json={"id": "srsc", "hierarchical": True})
        assert [v["id"] for v in mgr.get_vocabularies()] == ["srsc", "common_types"]
        assert mgr.get_vocabulary("srsc")["hierarchical"] is True

    def test_vocabulary_for_field(self, mgr, http, api_url):
        url = api_url("/submission/vocabularies/search/byMetadataAndCollection")
        http.add(responses.GET, url, json={"id": "common_types"})
        assert mgr.vocabulary_for_field("dc.type", COLL)["id"] == "common_types"
        assert dict(params(http)) == {"metadata": "dc.type", "collection": COLL}
        http.replace(responses.GET, url, status=204)
        assert mgr.vocabulary_for_field("dc.title", COLL) is None


class TestEntries:
    URL = "/submission/vocabularies/common_types/entries"

    def test_iter_entries_params(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(self.URL), json=hal_page("entries", [{"value": "Article"}]))
        assert mgr.get_entries("common_types") == [{"value": "Article"}]
        assert set(dict(params(http))) == {"page", "size"}
        mgr.get_entries("common_types", filter="Art", exact=True, max_results=5)
        assert ("filter", "Art") in params(http) and ("exact", "true") in params(http)
        mgr.get_entries("common_types", entry_id="VR1")
        assert ("entryID", "VR1") in params(http)
        mgr.search("common_types", "Bo")
        assert ("filter", "Bo") in params(http) and ("exact", "true") not in params(http)

    def test_invalid_combinations(self, mgr):
        with pytest.raises(ValueError):
            mgr.iter_entries("x", filter="a", entry_id="b")
        with pytest.raises(ValueError):
            mgr.iter_entries("x", exact=True)

    def test_not_scrollable(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/submission/vocabularies/srsc/entries"), status=422)
        with pytest.raises(DSpaceError):
            mgr.get_entries("srsc")

    def test_lookup_value_display_and_cache(self, mgr, http, api_url, hal_page):
        url = api_url(self.URL)
        http.add(responses.GET, url, json=hal_page("entries", [
            {"value": "Book chapter", "display": "Book chapter"},
            {"value": "Book", "display": "Buch"}]))
        assert mgr.lookup("common_types", "Book")["display"] == "Buch"
        calls = len(http.calls)
        assert mgr.lookup("common_types", "Book") and len(http.calls) == calls     # cached
        assert mgr.lookup("common_types", "Buch")["value"] == "Book"              # by display
        assert mgr.lookup("common_types", "Buch", match_display=False) is None
        assert mgr.lookup("common_types", "Unknown") is None

    def test_check_and_invalid_values(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(self.URL), json=hal_page("entries", [{"value": "Article"}]))
        result = mgr.check_values("common_types", ["Article", "Artikel", "Article"])
        assert list(result) == ["Article", "Artikel"] and result["Artikel"] is None
        assert mgr.invalid_values("common_types", ["Article", "Artikel"]) == ["Artikel"]

    def test_get_entry_by_id(self, mgr, http, api_url, hal_page):
        url = api_url("/submission/vocabularies/srsc/entries")
        http.add(responses.GET, url, json=hal_page("entries", [{"value": "Family research"}]))
        assert mgr.get_entry_by_id("srsc", "VR131402")["value"] == "Family research"
        http.replace(responses.GET, url, json=hal_page("entries", []))
        assert mgr.get_entry_by_id("srsc", "nope") is None


class TestHierarchy:
    def test_detail_parent_children_top(self, mgr, http, api_url, hal_page):
        base = "/submission/vocabularyEntryDetails"
        http.add(responses.GET, api_url(f"{base}/srsc:SCB110"), json=detail("srsc:SCB110"))
        http.add(responses.GET, api_url(f"{base}/srsc:SCB110/parent"), json=detail("srsc:SCB11"))
        http.add(responses.GET, api_url(f"{base}/srsc:SCB11/children"),
                 json=hal_page("children", [detail("srsc:SCB110")]))
        http.add(responses.GET, api_url(f"{base}/search/top"),
                 json=hal_page("vocabularyEntryDetails", [detail("srsc:SCB11")]))
        assert mgr.get_entry_detail("srsc", "SCB110")["id"] == "srsc:SCB110"
        assert mgr.get_parent("srsc", "srsc:SCB110")["id"] == "srsc:SCB11"
        assert [c["id"] for c in mgr.get_children("srsc", "SCB11")] == ["srsc:SCB110"]
        assert [t["id"] for t in mgr.get_top_entries("srsc")] == ["srsc:SCB11"]
        assert ("vocabulary", "srsc") in params(http)

    def test_get_path(self, mgr, http, api_url):
        base = "/submission/vocabularyEntryDetails"
        http.add(responses.GET, api_url(f"{base}/srsc:C"), json=detail("srsc:C"))
        http.add(responses.GET, api_url(f"{base}/srsc:C/parent"), json=detail("srsc:B"))
        http.add(responses.GET, api_url(f"{base}/srsc:B/parent"), json=detail("srsc:A"))
        http.add(responses.GET, api_url(f"{base}/srsc:A/parent"), status=204)
        assert [e["id"] for e in mgr.get_path("srsc", "C")] == ["srsc:A", "srsc:B", "srsc:C"]
        assert [e["id"] for e in mgr.get_path("srsc", "C", max_depth=2)] == ["srsc:B", "srsc:C"]

    @pytest.fixture
    def tree(self, http, api_url, hal_page):
        """A (children) -> A1 (leaf), A2 (unknown -> asked, none); B (leaf)"""
        base = "/submission/vocabularyEntryDetails"
        http.add(responses.GET, api_url(f"{base}/search/top"),
                 json=hal_page("vocabularyEntryDetails", [detail("v:A", True), detail("v:B", False)]))
        http.add(responses.GET, api_url(f"{base}/v:A/children"),
                 json=hal_page("children", [detail("v:A1", False), detail("v:A2")]))
        http.add(responses.GET, api_url(f"{base}/v:A2/children"), json=hal_page("children", []))
        return http

    def test_iter_tree(self, mgr, tree):
        assert [(d, e["id"]) for d, e in mgr.iter_tree("v")] == [(0, "v:A"), (1, "v:A1"), (1, "v:A2"), (0, "v:B")]
        paths = [urlparse(c.request.url).path for c in tree.calls]
        asked = [p.rsplit("/", 2)[-2] for p in paths if p.endswith("/children")]
        assert asked == ["v:A", "v:A2"]                  # leaves marked hasChildren=false are not asked

    def test_iter_tree_depth_and_root(self, mgr, tree):
        assert [(d, e["id"]) for d, e in mgr.iter_tree("v", max_depth=0)] == [(0, "v:A"), (0, "v:B")]
        assert [e["id"] for _, e in mgr.iter_tree("v", root_id="A")] == ["v:A1", "v:A2"]
