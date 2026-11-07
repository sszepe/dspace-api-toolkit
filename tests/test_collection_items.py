"""
CollectionManager.get_collection_items / iter_collection_items / count_collection_items:
DSpace 7+ lists a collection's items through a discovery search scoped to it.
"""

from urllib.parse import parse_qsl, urlparse

import pytest
import responses

from dspace_api.collections import CollectionManager
from dspace_api.exceptions import BadRequestError

COLL = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture
def mgr(http_auth):
    return CollectionManager(http_auth)


def params(http, index=-1):
    return parse_qsl(urlparse(http.calls[index].request.url).query)


def search_page(uuids, number=0, total_pages=1, total=None, size=20):
    return {"_embedded": {"searchResult": {
        "_embedded": {"objects": [{"_embedded": {"indexableObject": {"uuid": u, "type": "item"}}} for u in uuids]},
        "page": {"number": number, "size": size, "totalPages": total_pages,
                 "totalElements": total if total is not None else len(uuids)}}}}


def test_no_legacy_endpoint(mgr, http, api_url):
    """The removed /core/collections/{uuid}/items endpoint must not be called."""
    http.add(responses.GET, api_url("/discover/search/objects"), json=search_page(["a"]))
    mgr.get_collection_items(COLL)
    assert all("/items" not in urlparse(c.request.url).path for c in http.calls)


def test_page_shape_and_params(mgr, http, api_url):
    http.add(responses.GET, api_url("/discover/search/objects"),
             json=search_page(["a", "b"], number=2, total_pages=7, total=33, size=5))
    out = mgr.get_collection_items(COLL, page=2, size=5, sort="dc.title,desc", all_languages=True)
    assert out == {"_embedded": {"items": [{"uuid": "a", "type": "item"}, {"uuid": "b", "type": "item"}]},
                   "page": {"number": 2, "size": 5, "totalElements": 33, "totalPages": 7}}
    assert params(http) == [("dsoType", "item"), ("scope", COLL), ("sort", "dc.title,desc"), ("page", "2"),
                            ("size", "5"), ("projection", "allLanguages")]


def test_query_filters_and_missing_page_info(mgr, http, api_url):
    response = {"_embedded": {"searchResult": {"_embedded": {"objects": [
        {"_embedded": {"indexableObject": {"uuid": "a"}}}, {"_embedded": {}}]}}}}
    http.add(responses.GET, api_url("/discover/search/objects"), json=response)
    out = mgr.get_collection_items(COLL, page=1, size=10, query="climate", filters={"type": "Article"},
                                   embed="thumbnail")
    assert out["_embedded"]["items"] == [{"uuid": "a"}]
    assert out["page"] == {"number": 1, "size": 10, "totalElements": 0, "totalPages": 0}
    p = params(http)
    assert ("query", "climate") in p and ("f.type", "Article,equals") in p and ("embed", "thumbnail") in p


def test_iter_all_pages(mgr, http, api_url):
    url = api_url("/discover/search/objects")
    http.add(responses.GET, url, json=search_page(["a", "b"], total_pages=2))
    http.add(responses.GET, url, json=search_page(["c"], number=1, total_pages=2))
    assert [i["uuid"] for i in mgr.iter_collection_items(COLL, page_size=2)] == ["a", "b", "c"]
    assert all(("scope", COLL) in params(http, i) and ("dsoType", "item") in params(http, i) for i in (0, 1))


def test_iter_max_results(mgr, http, api_url):
    http.add(responses.GET, api_url("/discover/search/objects"), json=search_page(["a", "b"], total_pages=3))
    assert [i["uuid"] for i in mgr.iter_collection_items(COLL, max_results=1)] == ["a"]


def test_count(mgr, http, api_url):
    http.add(responses.GET, api_url("/discover/search/objects"), json=search_page(["a"], total=12))
    assert mgr.count_collection_items(COLL, filters={"type": "Article"}) == 12
    assert ("size", "1") in params(http)


def test_discovery_manager_is_reused(mgr):
    assert mgr.discovery is mgr.discovery


def test_errors_propagate(mgr, http, api_url):
    http.add(responses.GET, api_url("/discover/search/objects"), status=400, json={"error": "bad"})
    with pytest.raises(BadRequestError):
        mgr.get_collection_items(COLL)


def test_all_languages_follows_client_default(mgr, http, api_url):
    http.add(responses.GET, api_url("/discover/search/objects"), json=search_page([]))
    mgr.get_collection_items(COLL)
    assert "projection" not in dict(params(http))
    mgr.config.all_languages = True
    mgr.get_collection_items(COLL)
    assert ("projection", "allLanguages") in params(http)
    mgr.get_collection_items(COLL, all_languages=False)
    assert "projection" not in dict(params(http))
