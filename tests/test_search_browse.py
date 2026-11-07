"""
Tests for the BaseManager-based discovery API: search configuration, filters,
paged search, facets and browse indexes.
"""

from urllib.parse import parse_qsl, urlparse

import pytest
import responses

from dspace_api.discovery import DiscoveryManager, filter_params
from dspace_api.exceptions import DSpaceError, NotFoundError

API = "http://localhost:8080/server/api"
COLL = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture
def mgr(http_auth):
    return DiscoveryManager(http_auth)


def params(http, index=-1):
    return parse_qsl(urlparse(http.calls[index].request.url).query)


def search_page(objects, total_pages=1, total=None):
    return {"_embedded": {"searchResult": {
        "_embedded": {"objects": [{"hitHighlights": {}, "_embedded": {"indexableObject": o}} for o in objects]},
        "page": {"totalPages": total_pages, "totalElements": total if total is not None else len(objects)}}}}


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

class TestFilterParams:
    def test_variants(self):
        assert filter_params(None) == [] and filter_params({}) == []
        assert filter_params({"author": "Doe, Jane"}) == [("f.author", "Doe, Jane,equals")]
        assert filter_params({"subject": ["a", ("b", "notequals")]}) == [
            ("f.subject", "a,equals"), ("f.subject", "b,notequals")]
        assert filter_params({"title": ("rain",)}) == [("f.title", "rain,equals")]
        assert filter_params({"dateIssued": "[2020 TO 2026],equals"}) == [("f.dateIssued", "[2020 TO 2026],equals")]
        assert filter_params({"author": "Doe, Jane,contains"}) == [("f.author", "Doe, Jane,contains")]
        assert filter_params([("author", "1234", "authority"), ("type", "Article")]) == [
            ("f.author", "1234,authority"), ("f.type", "Article,equals")]

    def test_unknown_operator(self):
        with pytest.raises(ValueError):
            filter_params({"title": ("x", "startswith")})


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_config(self, mgr, http, api_url):
        config = {"filters": [{"filter": "author"}, {"filter": "subject"}], "sortOptions": [{"name": "dc.title"}]}
        http.add(responses.GET, api_url("/discover/search"), json=config)
        assert mgr.get_search_config(scope=COLL, configuration="publication") == config
        assert params(http) == [("scope", COLL), ("configuration", "publication")]
        assert mgr.get_search_filters() == ["author", "subject"]
        assert mgr.get_sort_options() == [{"name": "dc.title"}]

    def test_search_config_without_lists(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/search"), json={})
        assert mgr.get_search_filters() == [] and mgr.get_sort_options() == []

    def test_search_params(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/search/objects"), json=search_page([{"uuid": "a"}]))
        mgr.search("dc.title:x", dso_type="ITEM", scope=COLL, configuration="c", filters={"author": "Doe"},
                   sort="dc.title,ASC", page=2, size=5, embed="thumbnail")
        assert params(http) == [("query", "dc.title:x"), ("dsoType", "item"), ("scope", COLL),
                                ("configuration", "c"), ("sort", "dc.title,ASC"), ("f.author", "Doe,equals"),
                                ("page", "2"), ("size", "5"), ("embed", "thumbnail")]

    def test_invalid_dso_type(self, mgr):
        with pytest.raises(ValueError):
            mgr.search(dso_type="bitstream")

    def test_search_result(self):
        assert DiscoveryManager.search_result({}) == ([], {})

    def test_iter_search_pages(self, mgr, http, api_url):
        url = api_url("/discover/search/objects")
        http.add(responses.GET, url, json=search_page([{"uuid": "a"}, {"uuid": "b"}], total_pages=2))
        http.add(responses.GET, url, json=search_page([{"uuid": "c"}], total_pages=2))
        assert [o["uuid"] for o in mgr.iter_search("x", page_size=2)] == ["a", "b", "c"]
        assert [dict(params(http, i))["page"] for i in (0, 1)] == ["0", "1"]

    def test_iter_search_max_and_hits(self, mgr, http, api_url):
        url = api_url("/discover/search/objects")
        http.add(responses.GET, url, json=search_page([{"uuid": "a"}, {"uuid": "b"}], total_pages=5))
        assert [o["uuid"] for o in mgr.iter_search(max_results=1)] == ["a"]
        hits = list(mgr.iter_search(with_hits=True, max_results=2))
        assert "hitHighlights" in hits[0]

    def test_iter_search_without_page_info(self, mgr, http, api_url):
        url = api_url("/discover/search/objects")
        page = search_page([{"uuid": "a"}])
        del page["_embedded"]["searchResult"]["page"]
        broken = {"_embedded": {"searchResult": {"_embedded": {"objects": [{"_embedded": {}}, {}]}}}}
        http.add(responses.GET, url, json=broken)        # full page without indexableObjects, no page info
        http.add(responses.GET, url, json=page)          # short page -> last
        assert [o["uuid"] for o in mgr.iter_search(page_size=2)] == ["a"]
        http.replace(responses.GET, url, json=search_page([]))
        assert list(mgr.iter_search()) == []

    def test_iter_search_stops_on_repeated_page(self, mgr, http, api_url):
        """A server that ignores page= and keeps sending the same full page must not loop forever."""
        http.add(responses.GET, api_url("/discover/search/objects"),
                 json={"_embedded": {"searchResult": {"_embedded": {"objects": [
                     {"_embedded": {"indexableObject": {"uuid": "a"}}}]}}}})
        assert [o["uuid"] for o in mgr.iter_search(page_size=1)] == ["a"]
        assert len(http.calls) == 2

    def test_count(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/search/objects"), json=search_page([{"uuid": "a"}], total=42))
        assert mgr.count("x", filters={"type": "Article"}) == 42
        assert dict(params(http))["size"] == "1"
        http.replace(responses.GET, api_url("/discover/search/objects"), json={})
        assert mgr.count() == 0


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------

FACETS = {"_embedded": {"facets": [
    {"name": "author", "_embedded": {"values": [{"label": "Doe", "count": 3}]}},
    {"name": "subject", "_embedded": {"values": []}},
]}}


class TestFacets:
    def test_search_facets(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/search/facets"), json=FACETS)
        assert [f["name"] for f in mgr.search_facets("x", scope=COLL)] == ["author", "subject"]
        assert [f["name"] for f in mgr.search_facets(names=["subject"])] == ["subject"]
        http.replace(responses.GET, api_url("/discover/search/facets"), json={})
        assert mgr.search_facets() == []

    def test_list_facets(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/facets"), json=FACETS)
        assert len(mgr.list_facets(configuration="default")) == 2
        assert params(http) == [("configuration", "default")]
        http.replace(responses.GET, api_url("/discover/facets"), json={})
        assert mgr.list_facets() == []

    def test_get_facet_params(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/facets/author"), json={"name": "author"})
        mgr.get_facet("author", query="q", scope=COLL, filters={"type": "Article"}, prefix="Do", sort="index",
                      page=1, size=10)
        assert params(http) == [("query", "q"), ("scope", COLL), ("sort", "index"), ("f.type", "Article,equals"),
                                ("prefix", "Do"), ("page", "1"), ("size", "10")]

    def test_iter_facet_values_follows_has_more_and_next(self, mgr, http, api_url):
        url = api_url("/discover/facets/subject")
        http.add(responses.GET, url, json={"hasMore": True, "_embedded": {"values": [{"label": "a", "count": 2}]}})
        http.add(responses.GET, url, json={"_links": {"next": {"href": "..."}},
                                           "_embedded": {"values": [{"label": "b", "count": "1"}]}})
        http.add(responses.GET, url, json={"_embedded": {"values": [{"label": "c"}]}})
        assert mgr.facet_counts("subject", page_size=1, scope=COLL) == {"a": 2, "b": 1, "c": 0}
        assert [dict(params(http, i))["page"] for i in range(3)] == ["0", "1", "2"]

    def test_iter_facet_values_stops(self, mgr, http, api_url):
        url = api_url("/discover/facets/subject")
        http.add(responses.GET, url, json={"hasMore": True, "_embedded": {"values": [{"label": "a"}, {"label": "b"}]}})
        assert len(list(mgr.iter_facet_values("subject", max_results=1))) == 1
        assert len(list(mgr.iter_facet_values("subject"))) == 2      # same page again -> stop
        http.replace(responses.GET, url, json={"hasMore": True})
        assert list(mgr.iter_facet_values("subject")) == []


# ---------------------------------------------------------------------------
# Browse
# ---------------------------------------------------------------------------

class TestBrowse:
    def test_indexes(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/discover/browses"), json=hal_page("browses", [{"id": "author"}]))
        http.add(responses.GET, api_url("/discover/browses/author"), json={"id": "author", "browseType": "valueList"})
        assert mgr.get_browse_indexes() == [{"id": "author"}]
        assert mgr.get_browse_index("author")["browseType"] == "valueList"

    def test_unknown_index(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/browses/nope"), status=404)
        with pytest.raises(NotFoundError):
            mgr.get_browse_index("nope")

    def test_find_browse_index(self, mgr, http, api_url):
        url = api_url("/discover/browses/search/byFields")
        http.add(responses.GET, url, json={"id": "author"})
        assert mgr.find_browse_index(["dc.contributor.author", "dc.creator"])["id"] == "author"
        assert params(http) == [("fields", "dc.contributor.author"), ("fields", "dc.creator")]
        http.replace(responses.GET, url, status=204)
        assert mgr.find_browse_index("dc.title") is None
        with pytest.raises(ValueError):
            mgr.find_browse_index([])

    def test_entries(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/discover/browses/author/entries"),
                 json=hal_page("browseEntries", [{"value": "Doe", "count": 2}], total_pages=1))
        assert mgr.iter_browse_entries("author", scope=COLL, sort="default,asc").__next__()["value"] == "Doe"
        assert ("scope", COLL) in params(http) and ("sort", "default,asc") in params(http)

    def test_starts_with_sends_no_page(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/browses/author/entries"),
                 json={"_embedded": {"browseEntries": [{"value": "Ma"}, {"value": "Mb"}, {"value": "Mc"}]}})
        assert [e["value"] for e in mgr.iter_browse_entries("author", starts_with="M", max_results=2)] == ["Ma", "Mb"]
        assert params(http) == [("startsWith", "M"), ("size", "2")]
        http.replace(responses.GET, api_url("/discover/browses/author/entries"), json={})
        assert list(mgr.iter_browse_entries("author", starts_with="Z")) == []

    def test_items(self, mgr, http, api_url, hal_page):
        url = api_url("/discover/browses/author/items")
        http.add(responses.GET, url, json=hal_page("items", [{"uuid": "i"}]))
        assert list(mgr.iter_browse_items("author", value="Doe, Jane", scope=COLL, all_languages=True))
        p = params(http)
        assert ("filterValue", "Doe, Jane") in p and ("projection", "allLanguages") in p
        list(mgr.iter_browse_items("author", authority="auth-1"))
        assert ("filterAuthority", "auth-1") in params(http)
        with pytest.raises(ValueError):
            mgr.iter_browse_items("author", value="a", authority="b")

    def test_flat_browse_with_starts_with(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/browses/dateissued/items"),
                 json={"_embedded": {"items": [{"uuid": "i"}]}})
        items = list(mgr.iter_browse_items("dateissued", starts_with="2020", sort="dateissued,desc",
                                           embed="thumbnail"))
        assert items == [{"uuid": "i"}]
        p = params(http)
        assert ("startsWith", "2020") in p and ("embed", "thumbnail") in p and "page" not in dict(p)

    def test_entry_items(self, mgr, http, api_url, hal_page):
        href = f"{API}/discover/browses/author/items?filterValue=Doe"
        http.add(responses.GET, api_url("/discover/browses/author/items"), json=hal_page("items", [{"uuid": "i"}]))
        assert list(mgr.iter_entry_items({"_links": {"items": {"href": href}}})) == [{"uuid": "i"}]
        assert ("filterValue", "Doe") in params(http)
        with pytest.raises(ValueError):
            list(mgr.iter_entry_items({"value": "x"}))

    def test_errors_propagate(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/browses/author/entries"), status=422)
        with pytest.raises(DSpaceError):
            list(mgr.iter_browse_entries("author"))
