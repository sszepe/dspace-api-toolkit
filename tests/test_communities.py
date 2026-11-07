"""
Tests for community management (communities.py)
"""

import json

import pytest
import responses

from dspace_api.communities import CommunityManager, build_dso_metadata

C1 = "cccccccc-cccc-cccc-cccc-cccccccccccc"
G1 = "11111111-1111-1111-1111-111111111111"
E1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def mgr(http_auth):
    return CommunityManager(http_auth)


def last(http):
    return http.calls[-1].request


class TestBuildMetadata:
    def test_fields_language_and_empty_values(self):
        md = build_dso_metadata(" Music ", description="<p>Desc</p>", abstract="  ", rights=None,
                                language="en", **{"x.flag": True, "x.off": False, "x.num": 3})
        assert md == {
            "dc.title": [{"value": "Music", "language": "en"}],
            "dc.description": [{"value": "<p>Desc</p>", "language": "en"}],
            "x.flag": [{"value": "true", "language": "en"}],
            "x.off": [{"value": "false", "language": "en"}],
            "x.num": [{"value": "3", "language": "en"}],
        }

    @pytest.mark.parametrize("title", ["", "   ", None])
    def test_title_required(self, title):
        with pytest.raises(ValueError):
            build_dso_metadata(title)


class TestRead:
    def test_get_and_lists(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"/core/communities/{C1}"), json={"uuid": C1})
        http.add(responses.GET, api_url("/core/communities/search/top"), json=hal_page("communities", [{"uuid": C1}]))
        http.add(responses.GET, api_url(f"/core/communities/{C1}/subcommunities"),
                 json=hal_page("subcommunities", [{"uuid": "s"}]))
        http.add(responses.GET, api_url(f"/core/communities/{C1}/collections"),
                 json=hal_page("collections", [{"uuid": "k"}]))
        assert mgr.get_community(C1)["uuid"] == C1
        assert mgr.get_top_communities() == [{"uuid": C1}]
        assert mgr.get_subcommunities(C1) == [{"uuid": "s"}]
        assert mgr.get_community_collections(C1) == [{"uuid": "k"}]

    def test_search_editable(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/search/objects"), json={"_embedded": {"searchResult": {
            "_embedded": {"objects": [{"_embedded": {"indexableObject": {"uuid": C1}}}]}, "page": {"totalPages": 1}}}})
        assert mgr.search_editable_communities("music") == [{"uuid": C1}]
        p = last(http).params
        assert (p["configuration"], p["dsoType"], p["sort"], p["query"]) == \
            ("editCommunity", "COMMUNITY", "dc.title,ASC", "music")


class TestWrite:
    def test_create_top_and_sub(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/core/communities"), status=201, json={"uuid": C1})
        mgr.create_community(build_dso_metadata("Music", description="d"))
        assert last(http).params == {}
        body = json.loads(last(http).body)
        assert body["name"] == "Music" and body["type"] == "community"
        assert body["metadata"]["dc.description"][0]["value"] == "d"
        mgr.create_community({"dc.title": "Sub"}, parent_uuid="parent")
        assert last(http).params == {"parent": "parent"}

    def test_create_requires_title(self, mgr):
        with pytest.raises(ValueError, match="dc.title"):
            mgr.create_community({"dc.description": "x"})

    def test_update_and_delete(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url(f"/core/communities/{C1}"), json={"uuid": C1})
        http.add(responses.DELETE, api_url(f"/core/communities/{C1}"), status=204)
        mgr.update_community_metadata(C1, "replace", {"dc.title": "New"})
        assert json.loads(last(http).body)[0] == {"op": "replace", "path": "/metadata/dc.title",
                                                  "value": [{"value": "New"}]}
        assert mgr.delete_community(C1) is True


class TestAdmins:
    URL = f"/core/communities/{C1}/adminGroup"

    def test_groups_property_is_lazy(self, mgr):
        assert mgr._group_manager is None and mgr.groups is mgr.groups

    def test_admin_group_lifecycle(self, mgr, http, api_url):
        http.add(responses.GET, api_url(self.URL), status=204)
        http.add(responses.POST, api_url(self.URL), status=201, json={"uuid": G1})
        http.add(responses.DELETE, api_url(self.URL), status=204)
        assert mgr.get_admin_group(C1) is None
        assert mgr.get_or_create_admin_group(C1, "Admins")["uuid"] == G1
        assert mgr.delete_admin_group(C1) is True

    def test_add_and_get_admins(self, mgr, http, api_url, hal_page):
        assert_empty = mgr  # noqa
        http.add(responses.GET, api_url(self.URL), status=204)
        assert mgr.get_admins(C1) == {"epersons": [], "subgroups": []}
        http.replace(responses.GET, api_url(self.URL), json={"uuid": G1})
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}/epersons"), json=hal_page("epersons", []))
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}/subgroups"), json=hal_page("subgroups", []))
        http.add(responses.POST, api_url(f"/eperson/groups/{G1}/epersons"), status=204)
        out = mgr.add_admins(C1, epersons=[E1])
        assert out["added_epersons"] == [E1]
        assert mgr.get_admins(C1) == {"epersons": [], "subgroups": []}
