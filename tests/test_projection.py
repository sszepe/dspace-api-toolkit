"""
Tests for projection / embed handling (projection.py) and its use in the managers
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import responses

from dspace_api.bundles import BundleManager
from dspace_api.communities import CommunityManager
from dspace_api.groups import GroupManager
from dspace_api.items import ItemManager
from dspace_api.permissions import PermissionManager
from dspace_api.projection import (
    ALL_LANGUAGES, FULL, apply_view, get_embedded, get_embedded_page, is_embedded_complete,
    normalize_embed, normalize_projection, view_params,
)

UUID = "11111111-1111-1111-1111-111111111111"


def page(key, objects, total=None):
    body = {"_embedded": {key: objects}, "page": {"number": 0, "totalPages": 1}}
    if total is not False:
        body["page"]["totalElements"] = len(objects) if total is None else total
    return body


# ---------------------------------------------------------------------------
# Normalising arguments
# ---------------------------------------------------------------------------

class TestNormalize:
    @pytest.mark.parametrize("value,expected", [
        (None, []), ("full", ["full"]), ("full, allLanguages", ["full", "allLanguages"]),
        (["full", "full", " allLanguages "], ["full", "allLanguages"]), ("", []), ([], []),
    ])
    def test_projection(self, value, expected):
        assert normalize_projection(value) == expected

    def test_embed_forms(self):
        assert normalize_embed(None) == ([], {})
        assert normalize_embed("bundles, owningCollection") == (["bundles", "owningCollection"], {})
        assert normalize_embed(["/bundles/bitstreams/", "bundles/bitstreams"]) == (["bundles/bitstreams"], {})
        assert normalize_embed({"bundles": 50, " bundles / bitstreams ": None}) == \
            (["bundles", "bundles/bitstreams"], {"bundles": 50})

    @pytest.mark.parametrize("bad", ["/", "a//b", ["ok", " / "]])
    def test_embed_invalid_path(self, bad):
        with pytest.raises(ValueError, match="Invalid embed path"):
            normalize_embed(bad)

    @pytest.mark.parametrize("size", [0, -1, True, "5", 2.5])
    def test_embed_invalid_size(self, size):
        with pytest.raises(ValueError, match="positive int"):
            normalize_embed({"bundles": size})


# ---------------------------------------------------------------------------
# Building request parameters
# ---------------------------------------------------------------------------

class TestApplyView:
    def test_nothing_requested_returns_params_unchanged(self):
        params = {"page": 0}
        assert apply_view(params) is params
        assert apply_view(None) is None

    def test_none_becomes_list(self):
        assert apply_view(None, "full", {"bundles": 5}) == [
            ("projection", "full"), ("embed", "bundles"), ("embed.size", "bundles=5")]

    def test_list_params_dedup(self):
        params = [("embed", "group"), ("page", 0)]
        assert apply_view(params, None, ["group", "eperson"]) == [("embed", "group"), ("page", 0), ("embed", "eperson")]

    def test_dict_params_merge_with_existing_values(self):
        out = apply_view({"projection": "preventMetadataSecurity", "embed": ["a"]}, ["full"], ["a", "b"])
        assert out == {"projection": ["preventMetadataSecurity", "full"], "embed": ["a", "b"]}
        assert apply_view({}, None, "x") == {"embed": "x"}

    def test_view_params_all_languages(self):
        cfg_on = SimpleNamespace(use_all_languages=lambda v=None: True if v is None else v)
        assert view_params(None, cfg_on) == [("projection", ALL_LANGUAGES)]
        assert view_params(None, cfg_on, False) is None
        assert view_params(None, cfg_on, None, [ALL_LANGUAGES, FULL]) == \
            [("projection", ALL_LANGUAGES), ("projection", FULL)]           # not added twice
        assert view_params(None, None, True, embed="x") == [("projection", ALL_LANGUAGES), ("embed", "x")]
        assert view_params(None, object()) is None                          # config without the feature


# ---------------------------------------------------------------------------
# Reading embedded results
# ---------------------------------------------------------------------------

ITEM = {
    "uuid": "i",
    "_embedded": {
        "owningCollection": {"uuid": "c", "_embedded": {"logo": {"uuid": "logo"}, "parentCommunity": None}},
        "bundles": page("bundles", [
            {"uuid": "b1", "_embedded": {"bitstreams": page("bitstreams", [{"uuid": "s1"}, {"uuid": "s2"}])}},
            {"uuid": "b2", "_embedded": {"bitstreams": page("bitstreams", [{"uuid": "s3"}], total=7)}},
            {"uuid": "b3"},                                          # bitstreams not embedded
        ]),
        "mappedCollections": [{"uuid": "m"}],                        # plain list form
        "relationships": page("relationships", [], total=0),
        "thumbnail": None,
    },
}


class TestGetEmbedded:
    def test_single_and_nested_single(self):
        assert get_embedded(ITEM, "owningCollection")["uuid"] == "c"
        assert get_embedded(ITEM, "owningCollection/logo") == {"uuid": "logo"}
        # an embedded null link reads the same as "not embedded"
        assert get_embedded(ITEM, "owningCollection/parentCommunity", "dflt") == "dflt"

    def test_lists_and_flattening(self):
        assert [b["uuid"] for b in get_embedded(ITEM, "bundles")] == ["b1", "b2", "b3"]
        assert [s["uuid"] for s in get_embedded(ITEM, "bundles/bitstreams")] == ["s1", "s2", "s3"]
        assert get_embedded(ITEM, "mappedCollections") == [{"uuid": "m"}]
        assert get_embedded(ITEM, "relationships") == []

    def test_missing(self):
        assert get_embedded(ITEM, "version") is None
        assert get_embedded(ITEM, "version", default=[]) == []
        assert get_embedded(ITEM, "thumbnail", "none") == "none"
        assert get_embedded(None, "bundles") is None
        assert get_embedded({"uuid": "x"}, "bundles") is None

    def test_page_info_and_completeness(self):
        items, total = get_embedded_page(ITEM, "bundles")
        assert len(items) == 3 and total == 3
        assert get_embedded_page(ITEM, "mappedCollections") == ([{"uuid": "m"}], None)
        assert get_embedded_page(ITEM, "owningCollection") == (None, None)        # a single resource
        assert get_embedded_page(ITEM, "version") == (None, None)
        bundles = get_embedded(ITEM, "bundles")
        assert is_embedded_complete(ITEM, "bundles")
        assert is_embedded_complete(bundles[0], "bitstreams")
        assert not is_embedded_complete(bundles[1], "bitstreams")                 # 1 of 7
        assert not is_embedded_complete(bundles[2], "bitstreams")                 # not embedded
        assert is_embedded_complete(ITEM, "mappedCollections")                    # no page info
        no_total = {"_embedded": {"x": page("x", [{"a": 1}], total=False)}}
        assert is_embedded_complete(no_total, "x")
        # empty embedded page: DSpace may send "_embedded": {} (or non-list values) without the list key
        empty = {"_embedded": {"x": {"_embedded": {"meta": {"k": 1}}, "page": {"totalElements": 0}}}}
        assert get_embedded_page(empty, "x") == ([], 0)
        assert get_embedded(empty, "x") == [] and is_embedded_complete(empty, "x")


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------

class TestManagers:
    def test_base_manager_get_sends_projection_and_embed(self, http_auth, http, api_url):
        http.add(responses.GET, api_url(f"/core/communities/{UUID}"), json={"uuid": UUID})
        CommunityManager(http_auth).get_community(UUID, projection="full", embed={"subcommunities": 10},
                                                  all_languages=True)
        assert http.calls[0].request.params == {
            "projection": [FULL, ALL_LANGUAGES], "embed": "subcommunities", "embed.size": "subcommunities=10"}

    def test_list_endpoints_and_searches_pass_it_through(self, http_auth, http, api_url):
        http.add(responses.GET, api_url("/core/communities/search/top"), json=page("communities", []))
        http.add(responses.GET, api_url("/discover/search/objects"), json={})
        mgr = CommunityManager(http_auth)
        mgr.get_top_communities(embed="logo")
        assert http.calls[-1].request.params["embed"] == "logo"
        mgr.search_editable_communities("x", projection="full")
        assert http.calls[-1].request.params["projection"] == "full"

    def test_projection_on_write_is_rejected(self, http_auth):
        with pytest.raises(ValueError, match="only apply to GET"):
            CommunityManager(http_auth)._request("POST", "http://x", "op", embed="bundles")

    def test_group_embed_object_and_embed_are_merged(self, http_auth, http, api_url):
        http.add(responses.GET, api_url(f"/eperson/groups/{UUID}"), json={"uuid": UUID})
        GroupManager(http_auth).get_group(UUID, embed_object=True, embed=["object", "subgroups"])
        assert http.calls[0].request.params == {"embed": ["object", "subgroups"]}

    def test_older_manager(self, mock_auth):
        mgr = ItemManager(mock_auth)
        mgr.session.get = Mock(return_value=Mock(status_code=200, json=Mock(return_value={})))
        mgr.get_item("i", embed=["owningCollection", "bundles/bitstreams"], projection="full")
        assert mgr.session.get.call_args.kwargs["params"] == [
            ("projection", "full"), ("embed", "owningCollection"), ("embed", "bundles/bitstreams")]
        mgr.get_items(embed={"owningCollection": None})
        assert mgr.session.get.call_args.kwargs["params"]["embed"] == "owningCollection"


class TestItemWithFiles:
    URL = "/core/items/i"

    def test_one_request_when_everything_is_embedded(self, http_auth, http, api_url):
        http.add(responses.GET, api_url(self.URL), json={"uuid": "i", "_embedded": {"bundles": page("bundles", [
            {"uuid": "b1", "name": "ORIGINAL", "_embedded": {"bitstreams": page("bitstreams", [{"uuid": "s1"}])}}])}})
        item, bundles = BundleManager(http_auth).get_item_with_files("i")
        assert item["uuid"] == "i"
        assert bundles[0]["name"] == "ORIGINAL" and bundles[0]["bitstreams"] == [{"uuid": "s1"}]
        assert len(http.calls) == 1
        assert http.calls[0].request.params == {
            "embed": ["bundles", "bundles/bitstreams"],
            "embed.size": ["bundles=100", "bundles/bitstreams=100"]}

    def test_truncated_lists_are_paged_in(self, http_auth, http, api_url, hal_page):
        http.add(responses.GET, api_url(self.URL), json={"uuid": "i", "_embedded": {"bundles": page("bundles", [
            {"uuid": "b1", "_embedded": {"bitstreams": page("bitstreams", [{"uuid": "s1"}], total=2)}},
            {"uuid": "b2", "_embedded": {"bitstreams": page("bitstreams", [{"uuid": "s9"}])}},
        ])}})
        http.add(responses.GET, api_url("/core/bundles/b1/bitstreams"),
                 json=hal_page("bitstreams", [{"uuid": "s1"}, {"uuid": "s2"}]))
        _, bundles = BundleManager(http_auth).get_item_with_files("i")
        assert [s["uuid"] for s in bundles[0]["bitstreams"]] == ["s1", "s2"]
        assert [s["uuid"] for s in bundles[1]["bitstreams"]] == ["s9"]
        assert len(http.calls) == 2                                   # only the truncated bundle was paged

    def test_server_ignoring_embeds_falls_back_to_paging(self, http_auth, http, api_url, hal_page):
        http.add(responses.GET, api_url(self.URL), json={"uuid": "i"})
        http.add(responses.GET, api_url("/core/items/i/bundles"), json=hal_page("bundles", [{"uuid": "b1"}]))
        http.add(responses.GET, api_url("/core/bundles/b1/bitstreams"), json=hal_page("bitstreams", [{"uuid": "s"}]))
        _, bundles = BundleManager(http_auth).get_item_with_files("i")
        assert bundles == [{"uuid": "b1", "bitstreams": [{"uuid": "s"}]}]

    def test_truncated_bundle_list_is_paged(self, http_auth, http, api_url, hal_page):
        http.add(responses.GET, api_url(self.URL), json={"uuid": "i", "_embedded": {
            "bundles": page("bundles", [{"uuid": "b1", "_embedded": {"bitstreams": page("bitstreams", [])}}], total=2)}})
        http.add(responses.GET, api_url("/core/items/i/bundles"),
                 json=hal_page("bundles", [{"uuid": "b1"}, {"uuid": "b2"}]))
        http.add(responses.GET, api_url("/core/bundles/b1/bitstreams"), json=hal_page("bitstreams", []))
        http.add(responses.GET, api_url("/core/bundles/b2/bitstreams"), json=hal_page("bitstreams", []))
        _, bundles = BundleManager(http_auth).get_item_with_files("i")
        assert [b["uuid"] for b in bundles] == ["b1", "b2"]

    def test_iter_item_resources_takes_item_name_from_response(self, http_auth, http, api_url):
        http.add(responses.GET, api_url(self.URL), json={"uuid": "i", "name": "My thesis",
                                                       "_embedded": {"bundles": page("bundles", [])}})
        perms = PermissionManager(http_auth)
        assert list(perms.iter_item_resources("i"))[0]["name"] == "My thesis"
        assert list(perms.iter_item_resources("i", item_name="Given"))[0]["name"] == "Given"
