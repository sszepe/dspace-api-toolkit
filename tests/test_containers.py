"""
Tests for the community / collection extras: logo, hierarchy, metadata patch,
authorized searches (_containers.py), community tree, collection license,
item template, default access conditions, mapped items and harvesting.
"""

import hashlib
import io
import json

import pytest
import responses

from dspace_api.collections import CollectionManager
from dspace_api.communities import CommunityManager
from dspace_api.exceptions import DSpaceError, NotFoundError, ServerError
from dspace_api.metadata import MetadataPatch

API = "http://localhost:8080/server/api"
TOP = "10000000-0000-0000-0000-000000000000"
SUB = "20000000-0000-0000-0000-000000000000"
COLL = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ITEM = "11111111-1111-1111-1111-111111111111"
ITEM2 = "22222222-2222-2222-2222-222222222222"
LOGO = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
TEMPLATE = "tttttttt-tttt-tttt-tttt-tttttttttttt"


@pytest.fixture
def communities(http_auth):
    return CommunityManager(http_auth)


@pytest.fixture
def collections(http_auth):
    return CollectionManager(http_auth)


def last(http, method=None):
    return [c.request for c in http.calls if method is None or c.request.method == method][-1]


def body(http, method=None):
    return json.loads(last(http, method).body)


# ---------------------------------------------------------------------------
# Shared container features
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["communities", "collections"])
class TestLogo:
    def test_upload_new_logo(self, kind, request, http, api_url):
        mgr = request.getfixturevalue(kind)
        logo_url = api_url(f"/core/{kind}/{TOP}/logo")
        http.add(responses.GET, logo_url, status=204)
        content = b"\x89PNG"
        http.add(responses.POST, logo_url, status=201,
                 json={"uuid": LOGO, "checkSum": {"value": hashlib.md5(content).hexdigest()}})
        assert mgr.set_logo(TOP, content, name="logo.png")["uuid"] == LOGO
        request_ = last(http, "POST")
        assert b'filename="logo.png"' in request_.body and b"image/png" in request_.body
        assert not [c for c in http.calls if c.request.method == "DELETE"]

    def test_replace_logo(self, kind, request, http, api_url, caplog):
        mgr = request.getfixturevalue(kind)
        logo_url = api_url(f"/core/{kind}/{TOP}/logo")
        http.add(responses.GET, logo_url, json={"uuid": "old"})
        http.add(responses.DELETE, api_url("/core/bitstreams/old"), status=204)
        http.add(responses.POST, logo_url, status=201, json={"uuid": LOGO, "checkSum": {"value": "deadbeef"}})
        mgr.set_logo(TOP, io.BytesIO(b"x"), name="logo.unknownext")
        assert b"application/octet-stream" in last(http, "POST").body
        assert last(http, "DELETE").url.endswith("/core/bitstreams/old")
        assert "differs from the local file" in caplog.text

    def test_get_and_delete(self, kind, request, http, api_url):
        mgr = request.getfixturevalue(kind)
        logo_url = api_url(f"/core/{kind}/{TOP}/logo")
        http.add(responses.GET, logo_url, status=204)
        assert mgr.get_logo(TOP) is None
        assert mgr.delete_logo(TOP) is False


class TestHierarchyAndMetadata:
    def test_parent_and_ancestors(self, collections, http, api_url):
        http.add(responses.GET, api_url(f"/core/collections/{COLL}/parentCommunity"), json={"uuid": SUB})
        http.add(responses.GET, api_url(f"/core/communities/{SUB}/parentCommunity"), json={"uuid": TOP})
        http.add(responses.GET, api_url(f"/core/communities/{TOP}/parentCommunity"), status=204)
        assert collections.get_parent_community(COLL, embed="logo")["uuid"] == SUB
        assert [c["uuid"] for c in collections.get_ancestors(COLL)] == [TOP, SUB]
        assert [c["uuid"] for c in collections.get_ancestors(COLL, max_depth=1)] == [SUB]

    def test_top_level_community(self, communities, http, api_url):
        http.add(responses.GET, api_url(f"/core/communities/{TOP}/parentCommunity"), status=204)
        assert communities.get_parent_community(TOP) is None
        assert communities.get_ancestors(TOP) == []

    def test_patch_metadata(self, communities, http, api_url):
        http.add(responses.PATCH, api_url(f"/core/communities/{TOP}"), json={"uuid": TOP})
        communities.patch_metadata(TOP, MetadataPatch().set("dc.description", "d"))
        assert body(http) == [{"op": "remove", "path": "/metadata/dc.description"},
                              {"op": "add", "path": "/metadata/dc.description/-", "value": {"value": "d"}}]
        with pytest.raises(ValueError):
            communities.patch_metadata(TOP, [])


class TestAuthorizedSearches:
    def test_communities(self, communities, http, api_url, hal_page):
        for method in ("findAdminAuthorized", "findEditAuthorized", "findAddAuthorized"):
            http.add(responses.GET, api_url(f"/core/communities/search/{method}"),
                     json=hal_page("communities", [{"uuid": TOP}, {"uuid": SUB}]))
        assert len(communities.find_admin_authorized()) == 2
        assert "query" not in last(http).params
        assert len(communities.find_edit_authorized("music", max_results=1)) == 1
        assert last(http).params["query"] == "music"
        assert len(communities.find_add_authorized(embed="logo")) == 2
        assert last(http).params["embed"] == "logo"

    def test_collections(self, collections, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/collections/search/findAdminAuthorized"),
                 json=hal_page("collections", [{"uuid": COLL}]))
        assert collections.find_admin_authorized("x") == [{"uuid": COLL}]

    @pytest.mark.parametrize("community, entity_type, method", [
        (None, None, "findSubmitAuthorized"),
        (None, "Publication", "findSubmitAuthorizedByEntityType"),
        (TOP, None, "findSubmitAuthorizedByCommunity"),
        (TOP, "Publication", "findSubmitAuthorizedByCommunityAndEntityType"),
    ])
    def test_submit_authorized_variants(self, collections, http, api_url, hal_page, community, entity_type, method):
        http.add(responses.GET, api_url(f"/core/collections/search/{method}"), json=hal_page("collections", []))
        collections.find_submit_authorized("q", entity_type=entity_type, community_uuid=community)
        params = last(http).params
        assert params.get("uuid") == community and params.get("entityType") == entity_type
        assert params["query"] == "q"


class TestCommunityTree:
    def test_iter_tree(self, communities, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/communities/search/top"),
                 json=hal_page("communities", [{"uuid": TOP}, {"uuid": "other"}]))
        http.add(responses.GET, api_url(f"/core/communities/{TOP}/collections"),
                 json=hal_page("collections", [{"uuid": "c-top"}]))
        http.add(responses.GET, api_url(f"/core/communities/{TOP}/subcommunities"),
                 json=hal_page("subcommunities", [{"uuid": SUB}]))
        http.add(responses.GET, api_url(f"/core/communities/{SUB}/collections"),
                 json=hal_page("collections", [{"uuid": COLL}]))
        for uuid in (SUB, "other"):
            http.add(responses.GET, api_url(f"/core/communities/{uuid}/subcommunities"),
                     json=hal_page("subcommunities", []))
        http.add(responses.GET, api_url("/core/communities/other/collections"), json=hal_page("collections", []))
        tree = [(d, k, o["uuid"]) for d, k, o in communities.iter_tree()]
        assert tree == [(0, "community", TOP), (1, "collection", "c-top"), (1, "community", SUB),
                        (2, "collection", COLL), (0, "community", "other")]

    def test_iter_tree_from_one_community_limited(self, communities, http, api_url):
        http.add(responses.GET, api_url(f"/core/communities/{TOP}"), json={"uuid": TOP})
        tree = list(communities.iter_tree(TOP, max_depth=0, include_collections=False))
        assert tree == [(0, "community", {"uuid": TOP})]

    def test_iter_tree_without_collections(self, communities, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"/core/communities/{TOP}"), json={"uuid": TOP})
        http.add(responses.GET, api_url(f"/core/communities/{TOP}/subcommunities"),
                 json=hal_page("subcommunities", [{"uuid": SUB}]))
        http.add(responses.GET, api_url(f"/core/communities/{SUB}/subcommunities"),
                 json=hal_page("subcommunities", []))
        assert [(d, o["uuid"]) for d, _, o in communities.iter_tree(TOP, include_collections=False)] == [
            (0, TOP), (1, SUB)]
        assert not [c for c in http.calls if c.request.url.endswith("/collections")]


# ---------------------------------------------------------------------------
# Collection-only features
# ---------------------------------------------------------------------------

class TestLicense:
    def test_get_and_set(self, collections, http, api_url):
        http.add(responses.GET, api_url(f"/core/collections/{COLL}/license"), json={"custom": False, "text": "T"})
        http.add(responses.PATCH, api_url(f"/core/collections/{COLL}"), json={"uuid": COLL})
        assert collections.get_license(COLL)["custom"] is False
        collections.set_license(COLL, "Our license")
        assert body(http) == [{"op": "remove", "path": "/metadata/dc.rights.license"},
                              {"op": "add", "path": "/metadata/dc.rights.license/-", "value": {"value": "Our license"}}]

    def test_reset_to_default(self, collections, http, api_url):
        get_url = api_url(f"/core/collections/{COLL}")
        http.add(responses.GET, get_url, json={"uuid": COLL, "metadata": {"dc.rights.license": [{"value": "x"}]}})
        http.add(responses.PATCH, get_url, json={"uuid": COLL})
        collections.set_license(COLL, None)
        assert body(http, "PATCH") == [{"op": "remove", "path": "/metadata/dc.rights.license"}]
        assert last(http, "GET").params == {"projection": "allLanguages"}

    def test_reset_without_custom_license(self, collections, http, api_url):
        http.add(responses.GET, api_url(f"/core/collections/{COLL}"), json={"uuid": COLL, "metadata": {}})
        assert collections.set_license(COLL, None)["uuid"] == COLL
        assert not [c for c in http.calls if c.request.method == "PATCH"]


class TestItemTemplate:
    def test_get(self, collections, http, api_url):
        url = api_url(f"/core/collections/{COLL}/itemtemplate")
        http.add(responses.GET, url, json={"uuid": TEMPLATE})
        assert collections.get_item_template(COLL, all_languages=True)["uuid"] == TEMPLATE
        http.replace(responses.GET, url, status=204)
        assert collections.get_item_template(COLL) is None

    def test_get_404_means_no_template(self, collections, http, api_url):
        http.add(responses.GET, api_url(f"/core/collections/{COLL}/itemtemplate"), status=404)
        http.add(responses.GET, api_url(f"/core/collections/{COLL}"), json={"uuid": COLL})
        assert collections.get_item_template(COLL) is None
        http.replace(responses.GET, api_url(f"/core/collections/{COLL}"), status=404)
        with pytest.raises(NotFoundError):
            collections.get_item_template(COLL)

    def test_set_creates(self, collections, http, api_url):
        url = api_url(f"/core/collections/{COLL}/itemtemplate")
        http.add(responses.GET, url, status=204)
        http.add(responses.POST, url, json={"uuid": TEMPLATE}, status=201)
        assert collections.set_item_template(COLL, {"dc.type": "Journal Article"})["uuid"] == TEMPLATE
        assert body(http, "POST") == {"metadata": {"dc.type": [{"value": "Journal Article"}]}, "inArchive": False,
                                      "discoverable": False, "withdrawn": False, "type": "item"}

    def test_set_updates(self, collections, http, api_url):
        http.add(responses.GET, api_url(f"/core/collections/{COLL}/itemtemplate"), json={"uuid": TEMPLATE})
        http.add(responses.PATCH, api_url(f"/core/itemtemplates/{TEMPLATE}"), json={"uuid": TEMPLATE})
        collections.set_item_template(COLL, {"dc.type": "Thesis"})
        assert body(http, "PATCH") == [{"op": "remove", "path": "/metadata/dc.type"},
                                       {"op": "add", "path": "/metadata/dc.type/-", "value": {"value": "Thesis"}}]
        collections.update_item_template(TEMPLATE, MetadataPatch().remove("dc.type"))
        assert body(http, "PATCH") == [{"op": "remove", "path": "/metadata/dc.type"}]
        with pytest.raises(ValueError):
            collections.update_item_template(TEMPLATE, {})

    def test_delete(self, collections, http, api_url):
        url = api_url(f"/core/collections/{COLL}/itemtemplate")
        http.add(responses.GET, url, json={"uuid": TEMPLATE})
        http.add(responses.DELETE, api_url(f"/core/itemtemplates/{TEMPLATE}"), status=204)
        assert collections.delete_item_template(COLL) is True
        http.replace(responses.GET, url, status=204)
        assert collections.delete_item_template(COLL) is False


class TestDefaultAccessAndMapping:
    def test_default_access_conditions(self, collections, http, api_url, hal_page):
        http.add(responses.GET, api_url("/authz/resourcepolicies/search/resource"),
                 json=hal_page("resourcepolicies", [{"id": 1, "action": "DEFAULT_BITSTREAM_READ"}]))
        assert collections.get_default_access_conditions(COLL)[0]["id"] == 1
        assert last(http).params == {"uuid": COLL, "action": "DEFAULT_BITSTREAM_READ", "embed": "group",
                                     "page": "0", "size": last(http).params["size"]}
        collections.get_default_access_conditions(COLL, embed=None)
        assert "embed" not in last(http).params

    def test_mapped_items(self, collections, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"/core/collections/{COLL}/mappedItems"),
                 json=hal_page("mappedItems", [{"uuid": ITEM}]))
        assert collections.get_mapped_items(COLL, all_languages=True) == [{"uuid": ITEM}]
        assert last(http).params["projection"] == "allLanguages"

    def test_map_unmap(self, collections, http, api_url):
        http.add(responses.POST, api_url(f"/core/items/{ITEM}/mappedCollections"), status=204)
        http.add(responses.DELETE, api_url(f"/core/items/{ITEM}/mappedCollections/{COLL}"), status=204)
        assert collections.map_item(COLL, ITEM) is True
        request = last(http)
        assert request.headers["Content-Type"] == "text/uri-list" and request.body == f"{API}/core/collections/{COLL}"
        assert collections.unmap_item(COLL, ITEM) is True

    def test_map_items_continues_after_errors(self, collections, http, api_url):
        http.add(responses.POST, api_url(f"/core/items/{ITEM}/mappedCollections"), status=204)
        http.add(responses.POST, api_url(f"/core/items/{ITEM2}/mappedCollections"), status=422)
        result = collections.map_items(COLL, [ITEM, ITEM2])
        assert result[ITEM] == "mapped" and result[ITEM2].startswith("error:")


class TestHarvester:
    def test_configs_and_get(self, collections, http, api_url):
        http.add(responses.GET, api_url("/config/harvestermetadata"), json={"configs": [{"id": "dc"}]})
        http.add(responses.GET, api_url(f"/core/collections/{COLL}/harvester"), json={"harvest_type": "NONE"})
        assert collections.get_harvester_metadata_configs() == [{"id": "dc"}]
        assert collections.get_harvester(COLL)["harvest_type"] == "NONE"

    def test_configs_missing(self, collections, http, api_url):
        http.add(responses.GET, api_url("/config/harvestermetadata"), json={})
        assert collections.get_harvester_metadata_configs() == []

    def test_set_and_disable(self, collections, http, api_url):
        url = api_url(f"/core/collections/{COLL}/harvester")
        http.add(responses.PUT, url, json={"harvest_type": "METADATA_ONLY"})
        result = collections.set_harvester(COLL, "https://example.org/oai/request", "col_1", "qdc")
        assert result["harvest_type"] == "METADATA_ONLY"
        assert body(http) == {"harvest_type": "METADATA_ONLY", "oai_source": "https://example.org/oai/request",
                              "oai_set_id": "col_1", "metadata_config_id": "qdc"}
        # servers that answer 204 -> settings are read back
        http.replace(responses.PUT, url, status=204)
        http.add(responses.GET, url, json={"harvest_type": "NONE"})
        assert collections.disable_harvesting(COLL) == {"harvest_type": "NONE"}
        assert body(http, "PUT") == {"harvest_type": "NONE"}

    def test_invalid_type_and_errors(self, collections, http, api_url):
        with pytest.raises(ValueError):
            collections.set_harvester(COLL, "x", harvest_type="EVERYTHING")
        http.add(responses.PUT, api_url(f"/core/collections/{COLL}/harvester"), status=422)
        with pytest.raises(DSpaceError):
            collections.set_harvester(COLL, "not-an-oai-endpoint")

    def test_server_error(self, collections, http, api_url):
        http.add(responses.GET, api_url(f"/core/collections/{COLL}/harvester"), status=500)
        with pytest.raises(ServerError):
            collections.get_harvester(COLL)
