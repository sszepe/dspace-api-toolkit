"""
Tests for the shared BaseManager plumbing (_base.py)
"""

import json
from unittest.mock import Mock

import pytest
import requests
import responses

from dspace_api._base import BaseManager
from dspace_api.exceptions import AuthorizationError, NotFoundError


@pytest.fixture
def base(http_auth):
    return BaseManager(http_auth)


class TestBaseManager:
    def test_session_follows_auth(self, http_auth):
        mgr = BaseManager(http_auth)
        http_auth.session = Mock()
        assert mgr.session is http_auth.session

    def test_url_and_uri_list(self, base, api_url):
        assert base._url("/core/items/", 5) == api_url("/core/items/5")
        assert base._uri_list("/core/x") == api_url("/core/x")

    def test_headers(self, base):
        h = base._headers()
        assert h["Content-Type"] == "application/json" and h["Accept"] == "application/json"
        assert "Content-Type" not in base._headers(None, accept="*/*")

    @pytest.mark.parametrize("href,expected", [
        ("http://localhost:8080/server/api/core/items/1", "http://localhost:8080/server/api/core/items/1"),
        ("/api/core/items/1", "http://localhost:8080/server/api/core/items/1"),
        ("core/items/1", "http://localhost:8080/server/api/core/items/1"),
        ("/core/items/1", "http://localhost:8080/server/api/core/items/1"),
    ])
    def test_resolve_href(self, base, href, expected):
        assert base.resolve_href(href) == expected

    def test_resolve_href_refuses_foreign_hosts(self, base):
        with pytest.raises(ValueError, match="Refusing"):
            base.resolve_href("https://evil.example.org/api/core/items/1")

    def test_resolve_api_path_when_base_url_has_no_api_suffix(self, base):
        base.config.dspace_url = "http://localhost:8080/rest"
        assert base.resolve_href("/api/x") == "http://localhost:8080/rest/api/x"

    def test_get_by_href(self, base, http, api_url):
        http.add(responses.GET, api_url("/eperson/epersons/e1"), json={"uuid": "e1"})
        assert base.get_by_href("/api/eperson/epersons/e1") == {"uuid": "e1"}

    def test_csrf_retry_and_token_update(self, base, http, api_url):
        http.add(responses.POST, api_url("/x"), status=403, headers={"DSPACE-XSRF-TOKEN": "fresh"})
        http.add(responses.POST, api_url("/x"), status=200, json={})
        base._request("POST", api_url("/x"), "op")
        assert http.calls[1].request.headers["X-XSRF-Token"] == "fresh"

    def test_no_retry_on_get_or_without_token(self, base, http, api_url):
        http.add(responses.GET, api_url("/x"), status=403, headers={"DSPACE-XSRF-TOKEN": "t"})
        with pytest.raises(AuthorizationError):
            base._request("GET", api_url("/x"), "op")
        http.add(responses.DELETE, api_url("/y"), status=403)
        with pytest.raises(AuthorizationError):
            base._request("DELETE", api_url("/y"), "op")
        assert len(http.calls) == 2

    def test_auth_without_update_token(self, dspace_config, http, api_url):
        fake = Mock(spec=["session", "config"])
        fake.session, fake.config = requests.Session(), dspace_config
        http.add(responses.GET, api_url("/x"), json={"a": 1})
        assert BaseManager(fake)._get_json(api_url("/x"), "op") == {"a": 1}

    def test_get_optional(self, base, http, api_url):
        http.add(responses.GET, api_url("/x"), status=204)
        assert base._get_optional(api_url("/x"), "op") is None
        http.replace(responses.GET, api_url("/x"), status=200, body=b"")
        assert base._get_optional(api_url("/x"), "op") is None
        http.replace(responses.GET, api_url("/x"), json={})
        assert base._get_optional(api_url("/x"), "op") is None
        http.replace(responses.GET, api_url("/x"), json={"a": 1})
        assert base._get_optional(api_url("/x"), "op") == {"a": 1}
        http.replace(responses.GET, api_url("/x"), status=404)
        with pytest.raises(NotFoundError):
            base._get_optional(api_url("/x"), "op")

    def test_json_or_none(self):
        assert BaseManager._json_or_none(Mock(content=b"")) is None
        assert BaseManager._json_or_none(Mock(content=b"{}", json=Mock(return_value={"a": 1}))) == {"a": 1}


class TestPaging:
    def test_alternative_keys_and_max_results(self, base, http, api_url, hal_page):
        http.add(responses.GET, api_url("/x"), json=hal_page("groups", [{"n": 1}, {"n": 2}], total_pages=2))
        http.add(responses.GET, api_url("/x"), json=hal_page("groups", [{"n": 3}], total_pages=2))
        assert [o["n"] for o in base._iter_embedded(api_url("/x"), ("subgroups", "groups"), "op")] == [1, 2, 3]
        http.replace(responses.GET, api_url("/x"), json=hal_page("groups", [{"n": 1}, {"n": 2}], total_pages=2))
        assert [o["n"] for o in base._iter_embedded(api_url("/x"), "groups", "op", max_results=1)] == [1]

    def test_missing_key_and_page_block(self, base, http, api_url):
        base.DEFAULT_PAGE_SIZE = 1
        http.add(responses.GET, api_url("/x"), json={"_embedded": {"k": [{"n": 1}]}})
        http.add(responses.GET, api_url("/x"), json={})
        assert len(list(base._iter_embedded(api_url("/x"), "k", "op"))) == 1
        base.DEFAULT_PAGE_SIZE = 10
        http.replace(responses.GET, api_url("/x"), json={"_embedded": {"k": [{"n": 1}]}})
        assert len(list(base._iter_embedded(api_url("/x"), "k", "op"))) == 1


class TestDiscover:
    @staticmethod
    def page(uuids, total):
        objs = [{"_embedded": {"indexableObject": {"uuid": u}}} for u in uuids] + [{"_embedded": {}}]
        return {"_embedded": {"searchResult": {"_embedded": {"objects": objs}, "page": {"totalPages": total}}}}

    def test_params_paging_and_limits(self, base, http, api_url):
        url = api_url("/discover/search/objects")
        http.add(responses.GET, url, json=self.page(["a", "b"], 2))
        http.add(responses.GET, url, json=self.page(["c"], 2))
        out = base._discover("  music ", dso_type="COMMUNITY", configuration="editCommunity",
                             scope="s", sort="dc.title,ASC")
        assert [o["uuid"] for o in out] == ["a", "b", "c"]
        assert http.calls[0].request.params == {"page": "0", "size": "20", "query": "music", "dsoType": "COMMUNITY",
                                                "configuration": "editCommunity", "scope": "s",
                                                "sort": "dc.title,ASC"}
        http.replace(responses.GET, url, json=self.page(["a", "b"], 2))
        assert [o["uuid"] for o in base._discover(max_results=1)] == ["a"]
        assert "query" not in http.calls[-1].request.params

    def test_empty(self, base, http, api_url):
        http.add(responses.GET, api_url("/discover/search/objects"), json={})
        assert base._discover() == []


class TestReferencesAndMetadata:
    def test_uuid_of(self):
        u = "11111111-1111-1111-1111-111111111111"
        assert BaseManager._uuid_of({"uuid": u}) == u
        assert BaseManager._uuid_of({"id": "x"}) == "x"
        assert BaseManager._uuid_of(u) == u
        assert BaseManager._uuid_of(f"http://h/api/eperson/groups/{u}") == u
        assert BaseManager._uuid_of("/api/eperson/groups/not-a-uuid") is None
        assert BaseManager._uuid_of("Anonymous") is None
        assert BaseManager._uuid_of(42) is None

    def test_format_metadata(self, base):
        assert base._format_metadata_field("a") == [{"value": "a"}]
        assert base._format_metadata_field({"value": "a"}) == [{"value": "a"}]
        assert base._format_metadata_field(["a", {"value": "b"}, 1]) == [{"value": "a"}, {"value": "b"}, {"value": "1"}]
        assert base._format_metadata_field(3) == [{"value": "3"}]
        assert base._format_metadata(None) == {}

    def test_metadata_patch_ops(self, base):
        assert base._metadata_patch_ops("remove", {"f": None}) == [{"op": "remove", "path": "/metadata/f"}]
        assert base._metadata_patch_ops("add", {"f": "v"}) == [{"op": "add", "path": "/metadata/f",
                                                                "value": [{"value": "v"}]}]
        with pytest.raises(ValueError):
            base._metadata_patch_ops("move", {})

    def test_upsert_ops(self, base):
        ops = BaseManager._upsert_ops({"a": [{"value": "x"}], "b": []}, {"a": "1", "b": "2"},
                                      base._format_metadata_field)
        assert [o["op"] for o in ops] == ["replace", "add"]
