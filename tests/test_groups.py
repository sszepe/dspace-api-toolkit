"""
Tests for DSpace group management (groups.py)
"""

import json

import pytest
import responses

from dspace_api.exceptions import NotFoundError
from dspace_api.groups import GroupManager

G1 = "11111111-1111-1111-1111-111111111111"
G2 = "22222222-2222-2222-2222-222222222222"
G3 = "33333333-3333-3333-3333-333333333333"
E1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
E2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
COLL = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture
def mgr(http_auth):
    return GroupManager(http_auth)


def last(http):
    return http.calls[-1].request


class TestLookup:
    def test_get_group_with_and_without_embed(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}"), json={"uuid": G1})
        mgr.get_group(G1)
        assert "embed" not in last(http).params
        mgr.get_group({"uuid": G1}, embed_object=True)
        assert last(http).params == {"embed": "object"}

    def test_iter_groups(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/groups"), json=hal_page("groups", [{"uuid": G1}]))
        assert list(mgr.iter_groups()) == [{"uuid": G1}]

    def test_search_groups(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/groups/search/byMetadata"),
                 json=hal_page("groups", [{"uuid": G1}, {"uuid": G2}]))
        assert len(mgr.search_groups("lib", max_results=1)) == 1
        assert last(http).params["query"] == "lib" and "embed" not in last(http).params
        mgr.search_groups("lib", embed_object=True)
        assert last(http).params["embed"] == "object"

    def test_search_not_member_of(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/groups/search/isNotMemberOf"), json=hal_page("groups", []))
        mgr.search_groups_not_member_of(G1, "x")
        assert last(http).params == {"group": G1, "query": "x", "embed": "object", "page": "0", "size": "100"}
        mgr.search_groups_not_member_of(G1, embed_object=False)
        assert "embed" not in last(http).params

    def test_find_by_name_and_resolve(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/groups/search/byMetadata"),
                 json=hal_page("groups", [{"uuid": G2, "name": "Library Staff"}, {"uuid": G1, "name": "Library"}]))
        assert mgr.find_group_by_name("library")["uuid"] == G1
        assert mgr.resolve_group("Library") == G1
        assert mgr.resolve_group("LIBRARY") == G1   # cached, case-insensitive
        assert len(http.calls) == 2
        assert mgr.find_group_by_name("Nope") is None

    def test_resolve_errors(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/groups/search/byMetadata"), json=hal_page("groups", []))
        with pytest.raises(NotFoundError):
            mgr.resolve_group("Ghosts")
        for bad in ("", 42, None):
            with pytest.raises(ValueError):
                mgr.resolve_group(bad)

    def test_resolve_href_and_dict(self, mgr):
        assert mgr.resolve_group(f"http://localhost:8080/server/api/eperson/groups/{G1}") == G1
        assert mgr.resolve_group({"uuid": G2}) == G2

    def test_get_group_object(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}/object"), json={"type": "collection"})
        assert mgr.get_group_object(G1) == {"type": "collection"}
        http.replace(responses.GET, api_url(f"/eperson/groups/{G1}/object"), status=204)
        assert mgr.get_group_object(G1) is None

    def test_epersons_manager_is_lazy(self, mgr):
        assert mgr._eperson_manager is None
        assert mgr.epersons is mgr.epersons


class TestWrite:
    def test_create_group(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/eperson/groups"), status=201, json={"uuid": G1})
        mgr.create_group("Editors", description="Edit stuff")
        assert json.loads(last(http).body) == {"name": "Editors",
                                               "metadata": {"dc.description": [{"value": "Edit stuff"}]}}
        mgr.create_group("Editors", description="ignored", metadata={"dc.description": "explicit"})
        assert json.loads(last(http).body)["metadata"]["dc.description"] == [{"value": "explicit"}]

    def test_get_or_create(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/groups/search/byMetadata"),
                 json=hal_page("groups", [{"uuid": G1, "name": "A"}]))
        assert mgr.get_or_create_group("A")["uuid"] == G1
        http.add(responses.POST, api_url("/eperson/groups"), status=201, json={"uuid": G2})
        assert mgr.get_or_create_group("B")["uuid"] == G2

    def test_rename_and_metadata_clear_cache(self, mgr, http, api_url):
        mgr._name_cache["x"] = G1
        http.add(responses.PATCH, api_url(f"/eperson/groups/{G1}"), json={"uuid": G1})
        mgr.rename_group(G1, "New")
        assert json.loads(last(http).body) == [{"op": "replace", "path": "/name", "value": "New"}]
        assert mgr._name_cache == {}
        mgr.update_group_metadata(G1, "add", {"dc.description": "d"})
        assert json.loads(last(http).body)[0]["path"] == "/metadata/dc.description"

    def test_delete_group(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url(f"/eperson/groups/{G1}"), status=204)
        assert mgr.delete_group(G1) is True


class TestMembers:
    def test_get_subgroups_reads_either_key(self, mgr, http, api_url, hal_page):
        url = api_url(f"/eperson/groups/{G1}/subgroups")
        http.add(responses.GET, url, json=hal_page("subgroups", [{"uuid": G2}]))
        assert mgr.get_subgroups(G1) == [{"uuid": G2}]
        assert last(http).params["embed"] == "object"
        http.replace(responses.GET, url, json=hal_page("groups", [{"uuid": G3}]))
        assert mgr.get_subgroups(G1, embed_object=False) == [{"uuid": G3}]
        assert "embed" not in last(http).params
        assert len(http.calls) == 2   # one request each

    def test_add_and_remove_subgroup(self, mgr, http, api_url):
        http.add(responses.POST, api_url(f"/eperson/groups/{G1}/subgroups"), status=204)
        http.add(responses.DELETE, api_url(f"/eperson/groups/{G1}/subgroups/{G2}"), status=204)
        assert mgr.add_subgroup(G1, G2) is True
        assert last(http).headers["Content-Type"] == "text/uri-list"
        assert last(http).body == api_url(f"/eperson/groups/{G2}")
        assert mgr.remove_subgroup(G1, G2) is True

    def test_epersons(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}/epersons"), json=hal_page("epersons", [{"uuid": E1}]))
        http.add(responses.POST, api_url(f"/eperson/groups/{G1}/epersons"), status=204)
        http.add(responses.DELETE, api_url(f"/eperson/groups/{G1}/epersons/{E1}"), status=204)
        http.add(responses.GET, api_url("/eperson/epersons/search/byEmail"), json={"uuid": E1})
        assert mgr.get_group_epersons(G1) == [{"uuid": E1}]
        assert mgr.add_eperson(G1, "jane@example.org") is True
        assert last(http).body == api_url(f"/eperson/epersons/{E1}")
        assert mgr.remove_eperson(G1, E1) is True

    def test_add_members_skips_existing(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}/epersons"), json=hal_page("epersons", [{"uuid": E1}]))
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}/subgroups"), json=hal_page("subgroups", [{"uuid": G2}]))
        http.add(responses.POST, api_url(f"/eperson/groups/{G1}/epersons"), status=204)
        http.add(responses.POST, api_url(f"/eperson/groups/{G1}/subgroups"), status=204)
        out = mgr.add_members(G1, epersons=[E1, E2, E2], subgroups=[G2, G3])
        assert out == {"added_epersons": [E2], "added_subgroups": [G3]}
        posts = [c.request.body for c in http.calls if c.request.method == "POST"]
        assert posts == [api_url(f"/eperson/epersons/{E2}"), api_url(f"/eperson/groups/{G3}")]

    def test_add_members_nothing_requested(self, mgr, http):
        assert mgr.add_members(G1) == {"added_epersons": [], "added_subgroups": []}
        assert len(http.calls) == 0

    def test_sync_members(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}/epersons"), json=hal_page("epersons", [{"uuid": E1}]))
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}/subgroups"), json=hal_page("subgroups", [{"uuid": G2}]))
        for m, path in ((responses.POST, "/epersons"), (responses.POST, "/subgroups"),
                        (responses.DELETE, f"/epersons/{E1}"), (responses.DELETE, f"/subgroups/{G2}")):
            http.add(m, api_url(f"/eperson/groups/{G1}{path}"), status=204)
        expected = {"added_epersons": [E2], "removed_epersons": [E1],
                    "added_subgroups": [G3], "removed_subgroups": [G2]}

        assert mgr.sync_members(G1, epersons=[E2], subgroups=[G3], dry_run=True) == expected
        assert all(c.request.method == "GET" for c in http.calls)

        assert mgr.sync_members(G1, epersons=[E2], subgroups=[G3]) == expected
        writes = [(c.request.method, c.request.url.split("/groups/")[1]) for c in http.calls
                  if c.request.method != "GET"]
        assert writes == [("POST", f"{G1}/epersons"), ("DELETE", f"{G1}/epersons/{E1}"),
                          ("POST", f"{G1}/subgroups"), ("DELETE", f"{G1}/subgroups/{G2}")]

    def test_sync_members_none_leaves_kind_untouched(self, mgr, http):
        assert mgr.sync_members(G1) == {"added_epersons": [], "removed_epersons": [],
                                        "added_subgroups": [], "removed_subgroups": []}
        assert len(http.calls) == 0


class TestRoleGroups:
    def test_role_validation(self, mgr):
        with pytest.raises(ValueError, match="dso_type"):
            mgr.get_role_group("item", COLL, "adminGroup")
        with pytest.raises(ValueError, match="Unknown community role"):
            mgr.get_role_group("community", COLL, "submittersGroup")
        with pytest.raises(ValueError, match="workflowGroups"):
            mgr.get_role_group("collection", COLL, "readers")
        with pytest.raises(ValueError):
            mgr.get_role_group("collection", COLL, "workflowGroups/")

    @pytest.mark.parametrize("dso,path,role", [
        ("community", "communities", "adminGroup"),
        ("collection", "collections", "itemReadGroup"),
        ("collection", "collections", "workflowGroups/reviewer"),
    ])
    def test_get_role_group(self, mgr, http, api_url, dso, path, role):
        url = api_url(f"/core/{path}/{COLL}/{role}")
        http.add(responses.GET, url, status=204)
        assert mgr.get_role_group(dso, COLL, role) is None
        http.replace(responses.GET, url, json={"uuid": G1})
        assert mgr.get_role_group(dso, COLL, role) == {"uuid": G1}

    def test_create_role_group(self, mgr, http, api_url):
        http.add(responses.POST, api_url(f"/core/collections/{COLL}/submittersGroup"), status=201, json={"uuid": G1})
        mgr.create_role_group("collection", COLL, "submittersGroup", description="Submitters")
        assert json.loads(last(http).body) == {"metadata": {"dc.description": [{"value": "Submitters"}]}}
        mgr.create_role_group("collection", COLL, "submittersGroup")
        assert json.loads(last(http).body) == {"metadata": {}}
        mgr.create_role_group("collection", COLL, "submittersGroup", description="x",
                              metadata={"dc.description": "y"})
        assert json.loads(last(http).body)["metadata"]["dc.description"] == [{"value": "y"}]

    def test_get_or_create_and_delete(self, mgr, http, api_url):
        url = api_url(f"/core/collections/{COLL}/adminGroup")
        http.add(responses.GET, url, json={"uuid": G1})
        assert mgr.get_or_create_role_group("collection", COLL, "adminGroup")["uuid"] == G1
        http.replace(responses.GET, url, status=204)
        http.add(responses.POST, url, status=201, json={"uuid": G2})
        assert mgr.get_or_create_role_group("collection", COLL, "adminGroup")["uuid"] == G2
        http.add(responses.DELETE, url, status=204)
        assert mgr.delete_role_group("collection", COLL, "adminGroup") is True

    def test_anonymous_is_not_a_read_role_group(self, mgr, http, api_url):
        # unrestricted collection: DSpace answers itemReadGroup with the Anonymous group
        url = api_url(f"/core/collections/{COLL}/itemReadGroup")
        http.add(responses.GET, url, json={"uuid": "anon", "name": "Anonymous"})
        assert mgr.get_role_group("collection", COLL, "itemReadGroup") is None
        http.add(responses.POST, url, status=201, json={"uuid": G2, "name": f"COLLECTION_{COLL}_ITEM_DEFAULT_READ"})
        assert mgr.get_or_create_role_group("collection", COLL, "itemReadGroup")["uuid"] == G2
        http.add(responses.GET, api_url(f"/core/collections/{COLL}/adminGroup"), json={"uuid": "x", "name": "Anonymous"})
        assert mgr.get_role_group("collection", COLL, "adminGroup")["uuid"] == "x"   # only read roles

    def test_ensure_role_members(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"/core/collections/{COLL}/itemReadGroup"), json={"uuid": G1})
        http.add(responses.GET, api_url(f"/eperson/groups/{G1}/epersons"), json=hal_page("epersons", []))
        http.add(responses.POST, api_url(f"/eperson/groups/{G1}/epersons"), status=204)
        out = mgr.ensure_role_members("collection", COLL, "itemReadGroup", epersons=[E1])
        assert out == {"group": {"uuid": G1}, "added_epersons": [E1], "added_subgroups": []}
