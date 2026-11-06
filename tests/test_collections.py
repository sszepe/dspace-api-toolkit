"""
Tests for DSpace collection module
"""

import pytest
from unittest.mock import Mock
from dspace_api.collections import CollectionManager
from dspace_api.config import Endpoints
from dspace_api.exceptions import BadRequestError, NotFoundError, ServerError

class TestCollectionManager:
    """Test cases for CollectionManager"""

    def test_init(self, mock_auth):
        """Test CollectionManager initialization"""
        manager = CollectionManager(mock_auth)

        assert manager.auth == mock_auth
        assert manager.session == mock_auth.session
        assert manager.config == mock_auth.config

    def test_get_collections_with_paging_and_sort(self, mock_auth, api_helpers, mock_responses):
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(
            manager.session, "get", 200,
            json_data={"_embedded": {"collections": []}, "page": {"size": 2, "number": 1}}
        )
        out = manager.get_collections(page=1, size=2, sort="name,asc")
        assert out["page"]["number"] == 1
        call = manager.session.get.call_args
        params = call[1]["params"]
        assert params == {"page": 1, "size": 2, "sort": "name,asc"}

    def test_get_collections_error_raises(self, mock_auth, api_helpers):
        """Test get_collections error handling"""
        manager = CollectionManager(mock_auth)
        # Trigger handle_response_error branch
        api_helpers.mock_api_response(manager.session, "get", 500, json_data={"error": "server"})
        with pytest.raises(Exception):
            manager.get_collections()

    def test_get_collection_success(self, mock_auth, api_helpers, mock_responses):
        """Test successful retrieval of a single collection by UUID"""
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(
            manager.session, "get", 200, json_data=mock_responses.collection_created
        )
        out = manager.get_collection("abc-uuid")
        assert out["uuid"] == mock_responses.collection_created["uuid"]
        # URL contains collection id
        url = manager.session.get.call_args[0][0]
        assert url.endswith("/core/collections/abc-uuid")

    def test_get_collection_error_raises(self, mock_auth, api_helpers):
        """Test get_collection error handling"""
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "get", 404, json_data={"error": "not found"})
        with pytest.raises(Exception):
            manager.get_collection("missing")

    def test_create_collection_success(self, mock_auth, mock_responses, api_helpers):
        """Test successful collection creation with parent community and dc.title metadata"""
        manager = CollectionManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session, "post", 201, json_data=mock_responses.collection_created
        )

        result = manager.create_collection("parent-community-uuid", "Test Collection")

        # Returned payload
        assert result == mock_responses.collection_created
        assert result["uuid"] == "new-collection-uuid"

        # URL and query param
        call_args = manager.session.post.call_args
        url = call_args[0][0]
        assert "/core/collections" in url
        assert "parent=parent-community-uuid" in url

        # Payload: name and metadata with dc.title present
        payload = call_args[1]["json"]
        assert payload["name"] == "Test Collection"
        assert payload["type"] == "collection"
        assert "metadata" in payload
        assert "dc.title" in payload["metadata"]

    def test_create_collection_error_raises(self, mock_auth, api_helpers):
        """Test create_collection error handling"""
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "post", 405, json_data={"error": "method not allowed"})
        with pytest.raises(Exception):
            manager.create_collection("p", "X")

    def test_update_collection_sets_json_patch_header_and_updates_title(self, mock_auth, api_helpers, mock_responses):
        """Test updating collection with JSON Patch and changing dc.title"""
        manager = CollectionManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session, "patch", 200, json_data=mock_responses.collection_updated
        )

        result = manager.update_collection(
            collection_uuid="abc-123",
            name="New Title"
        )

        # Call args
        call_args = manager.session.patch.call_args
        url = call_args[0][0]
        headers = call_args[1]["headers"]
        body = call_args[1]["json"]

        assert "/core/collections/abc-123" in url
        assert headers["Content-Type"] == "application/json-patch+json"
        assert isinstance(body, list)
        assert body[0]["path"] == "/metadata/dc.title"
        assert body[0]["value"] == {"value": "New Title"}
        assert result == mock_responses.collection_updated

    def test_update_collection_does_not_change_dspace_entity_type(self, mock_auth, api_helpers, mock_responses):
        """Test update_collection does not modify dspace.entity.type or cris.workspace.shared fields"""
        manager = CollectionManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session, "patch", 200, json_data=mock_responses.collection_updated
        )

        result = manager.update_collection(
            collection_uuid="abc-123",
            metadata={
                "dspace.entity.type": "SHOULD_NOT_BE_SENT",
                "cris.workspace.shared": True
            }
        )

        body = manager.session.patch.call_args[1]["json"]
        paths = [op["path"] for op in body]
        assert "/metadata/dspace.entity.type" not in paths
        assert "/metadata/cris.workspace.shared" in paths

    def test_update_collection_normalizes_metadata_types(self, mock_auth, api_helpers, mock_responses):
        """Test update_collection normalizes various metadata input types"""
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "patch", 200, json_data=mock_responses.collection_updated)

        manager.update_collection(
            "abc-123",
            metadata={
                "dc.subject": ["A", {"value": "B"}, 7],  # list of primitives & dicts
                "cris.workspace.shared": False,          # boolean primitive
                "foo": {"value": "bar"},                 # dict already shaped
            },
        )
        body = manager.session.patch.call_args[1]["json"]

        # dc.subject becomes list (or single) normalized
        subj = next(op for op in body if op["path"] == "/metadata/dc.subject")["value"]
        # We allow server to accept a single object if len==1; here len==3 => list
        assert isinstance(subj, list)
        assert subj[0] == {"value": "A"}
        assert subj[1] == {"value": "B"}
        assert subj[2] == {"value": 7}

        # boolean becomes {"value": False}
        ws = next(op for op in body if op["path"] == "/metadata/cris.workspace.shared")["value"]
        assert ws == {"value": False}

        # dict passed through
        foo = next(op for op in body if op["path"] == "/metadata/foo")["value"]
        assert foo == {"value": "bar"}

    def test_update_collection_204_triggers_followup_get(self, mock_auth, api_helpers, mock_responses):
        """Test update_collection handles 204 No Content by fetching updated resource"""
        manager = CollectionManager(mock_auth)
        # First, PATCH returns 204 (no content)
        api_helpers.mock_api_response(manager.session, "patch", 204, json_data=None)
        # Then, GET returns the updated resource
        api_helpers.mock_api_response(manager.session, "get", 200, json_data=mock_responses.collection_updated)

        out = manager.update_collection("abc-123", name="After 204")
        assert out["uuid"] == mock_responses.collection_updated["uuid"]
        # Ensure GET was called after PATCH
        assert manager.session.get.call_count == 1

    def test_update_collection_error_raises(self, mock_auth, api_helpers):
        """Test update_collection error handling"""
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "patch", 400, json_data={"error": "bad"})
        with pytest.raises(Exception):
            manager.update_collection("abc-123", name="bad")

    def test_delete_collection_success(self, mock_auth, api_helpers):
        """Test successful deletion of a collection"""
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "delete", 204, json_data=None)
        assert manager.delete_collection("abc-123") is True
        url = manager.session.delete.call_args[0][0]
        assert url.endswith("/core/collections/abc-123")

    def test_delete_collection_error_raises(self, mock_auth, api_helpers):
        """Test deletion error handling"""
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "delete", 403, json_data={"error": "forbidden"})
        with pytest.raises(Exception):
            manager.delete_collection("abc-123")


    def test_create_collection_with_existing_dc_title_leaves_it_as_is(self, mock_auth, api_helpers):
        """Test create_collection when dc.title is already provided in metadata"""
        manager = CollectionManager(mock_auth)
        # Simulate 200 (not just 201) to cover that branch too
        expected = {"uuid": "c-1", "name": "X", "metadata": {"dc.title": [{"value": "Y"}]}}
        api_helpers.mock_api_response(manager.session, "post", 200, json_data=expected)

        # Provide metadata that already has dc.title to enter the 'else' branch in create_collection
        out = manager.create_collection(
            parent_community_uuid="parent-uuid",
            name="X",
            metadata={"dc.title": [{"value": "Y"}], "dc.description": [{"value": "Desc"}]}
        )

        # Payload should keep provided title; url and query string should be correct
        call = manager.session.post.call_args
        url = call[0][0]
        payload = call[1]["json"]

        assert "/core/collections" in url and "parent=parent-uuid" in url
        assert payload["metadata"]["dc.title"] == [{"value": "Y"}]
        assert out == expected


    def test_get_collections_without_sort_excludes_sort_param(self, mock_auth, api_helpers):
        """Test get_collections without sort does not include sort param"""
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "get", 200, json_data={"_embedded": {"collections": []}, "page": {}})

        manager.get_collections(page=0, size=3)  # no sort
        params = manager.session.get.call_args[1]["params"]
        assert params == {"page": 0, "size": 3}

    def test_format_metadata_field_variants(self, mock_auth):
        """Test _format_metadata_field with various input types"""
        manager = CollectionManager(mock_auth)
        # str
        assert manager._format_metadata_field("hello") == [{"value": "hello"}]
        # list[str]
        assert manager._format_metadata_field(["a", "b"]) == [{"value": "a"}, {"value": "b"}]
        # list[dict] passthrough
        assert manager._format_metadata_field([{"value": "x"}, {"value": "y"}]) == [{"value": "x"}, {"value": "y"}]
        # list[mixed] -> stringify non-dict
        assert manager._format_metadata_field(["a", 7]) == [{"value": "a"}, {"value": "7"}]
        # other scalar -> stringify
        assert manager._format_metadata_field(123) == [{"value": "123"}]

    def test_update_collection_stringifies_nonstandard_types(self, mock_auth, api_helpers, mock_responses):
        """Test update_collection stringifies non-str/bool/int/float metadata values"""
        manager = CollectionManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "patch", 200, json_data=mock_responses.collection_updated)

        # Pass a value that's NOT str/bool/int/float, NOT list, NOT dict -> triggers else branch
        manager.update_collection(
            "abc-123",
            metadata={
                "weird": ("x", 1),   # tuple -> should go to: {"value": "('x', 1)"}
            },
        )

        body = manager.session.patch.call_args[1]["json"]
        weird_val = next(op for op in body if op["path"] == "/metadata/weird")["value"]
        assert weird_val == {"value": "('x', 1)"}

    def test_format_metadata_builds_dict(self, mock_auth):
        """Test _format_metadata builds full metadata dict from mixed input"""
        manager = CollectionManager(mock_auth)
        meta = {
            "dc.title": "T",
            "dc.subject": ["A", "B"],
        }
        out = manager._format_metadata(meta)
        assert out["dc.title"] == [{"value": "T"}]
        assert out["dc.subject"] == [{"value": "A"}, {"value": "B"}]


# ---------------------------------------------------------------------------
# Role groups, read restrictions, submission policies (real-session tests)
# ---------------------------------------------------------------------------

import json as _json
import responses as _responses
from dspace_api.collections import build_collection_metadata

COLL = "cccccccc-cccc-cccc-cccc-cccccccccccc"
GRP = "11111111-1111-1111-1111-111111111111"
ANON = {"uuid": "99999999-9999-9999-9999-999999999999", "name": "Anonymous"}


class TestCollectionAccessManagement:
    @pytest.fixture
    def mgr(self, http_auth):
        return CollectionManager(http_auth)

    def test_lazy_helpers(self, mgr):
        assert mgr._group_manager is None and mgr.groups is mgr.groups
        assert mgr._permission_manager is None and mgr.permissions is mgr.permissions

    def test_build_collection_metadata(self):
        md = build_collection_metadata("Theses", license="CC BY", entity_type="Publication",
                                       submission_definition="thesis", submission_definition_correction="thesis-edit",
                                       workspace_shared=False)
        assert md["dspace.entity.type"] == [{"value": "Publication", "language": None}]
        assert md["cris.workspace.shared"] == [{"value": "false", "language": None}]
        assert md["cris.submission.definition-correction"][0]["value"] == "thesis-edit"
        assert md["dc.rights.license"][0]["value"] == "CC BY"
        assert "cris.submission.definition" in md
        assert set(build_collection_metadata("T")) == {"dc.title"}

    def test_search_editable_collections(self, mgr, http, api_url):
        http.add(_responses.GET, api_url("/discover/search/objects"), json={})
        assert mgr.search_editable_collections("x") == []
        p = http.calls[-1].request.params
        assert p["configuration"] == "editCollection" and p["dsoType"] == "COLLECTION"

    def test_find_submit_authorized(self, mgr, http, api_url, hal_page):
        http.add(_responses.GET, api_url("/core/collections/search/findSubmitAuthorized"),
                 json=hal_page("collections", [{"uuid": COLL}]))
        http.add(_responses.GET, api_url("/core/collections/search/findSubmitAuthorizedByEntityType"),
                 json=hal_page("collections", []))
        assert mgr.find_submit_authorized() == [{"uuid": COLL}]
        assert "query" not in http.calls[-1].request.params
        mgr.find_submit_authorized(query="mus", entity_type="Person")
        assert http.calls[-1].request.params["entityType"] == "Person"
        assert http.calls[-1].request.params["query"] == "mus"

    def test_role_group_wrappers(self, mgr, http, api_url):
        url = api_url(f"/core/collections/{COLL}/submittersGroup")
        http.add(_responses.GET, url, status=204)
        http.add(_responses.POST, url, status=201, json={"uuid": GRP})
        http.add(_responses.DELETE, url, status=204)
        assert mgr.get_role_group(COLL, "submittersGroup") is None
        assert mgr.get_or_create_role_group(COLL, "submittersGroup")["uuid"] == GRP
        assert mgr.delete_role_group(COLL, "submittersGroup") is True
        assert mgr.get_role_members(COLL, "submittersGroup") == {"epersons": [], "subgroups": []}

    @pytest.mark.parametrize("method,role", [
        ("add_admins", "adminGroup"), ("add_submitters", "submittersGroup"),
        ("restrict_item_read", "itemReadGroup"), ("restrict_bitstream_read", "bitstreamReadGroup"),
    ])
    def test_member_shortcuts_create_role_group_and_add(self, mgr, http, api_url, hal_page, method, role):
        http.add(_responses.GET, api_url(f"/core/collections/{COLL}/{role}"), status=204)
        http.add(_responses.POST, api_url(f"/core/collections/{COLL}/{role}"), status=201, json={"uuid": GRP})
        http.add(_responses.GET, api_url(f"/eperson/groups/{GRP}/subgroups"), json=hal_page("subgroups", []))
        http.add(_responses.POST, api_url(f"/eperson/groups/{GRP}/subgroups"), status=204)
        other = "22222222-2222-2222-2222-222222222222"
        out = getattr(mgr, method)(COLL, subgroups=[other])
        assert out["group"]["uuid"] == GRP and out["added_subgroups"] == [other]

    def test_get_role_members_existing(self, mgr, http, api_url, hal_page):
        http.add(_responses.GET, api_url(f"/core/collections/{COLL}/adminGroup"), json={"uuid": GRP})
        http.add(_responses.GET, api_url(f"/eperson/groups/{GRP}/epersons"), json=hal_page("epersons", [{"uuid": "e"}]))
        http.add(_responses.GET, api_url(f"/eperson/groups/{GRP}/subgroups"), json=hal_page("subgroups", []))
        assert mgr.get_role_members(COLL, "adminGroup") == {"epersons": [{"uuid": "e"}], "subgroups": []}

    def test_is_read_restricted(self, mgr, http, api_url, hal_page):
        pol = lambda i, action, g: {"id": i, "action": action, "policyType": "TYPE_INHERITED",
                                    "_embedded": {"group": g}}
        http.add(_responses.GET, api_url("/authz/resourcepolicies/search/resource"), json=hal_page("resourcepolicies", [
            pol(1, "DEFAULT_ITEM_READ", ANON), pol(2, "DEFAULT_BITSTREAM_READ", {"uuid": GRP, "name": "Staff"})]))
        assert mgr.is_read_restricted(COLL) == {"items": False, "bitstreams": True}

    def test_submission_policies(self, mgr, http, api_url, hal_page):
        http.add(_responses.GET, api_url("/authz/resourcepolicies/search/resource"), json=hal_page("resourcepolicies", [
            {"id": 1, "action": "ADD", "policyType": "TYPE_SUBMISSION", "_embedded": {"group": ANON}},
            {"id": 2, "action": "READ", "policyType": "TYPE_INHERITED", "_embedded": {"group": ANON}}]))
        assert [p["id"] for p in mgr.get_submission_policies(COLL)] == [1]

        http.add(_responses.POST, api_url("/authz/resourcepolicies"), status=201, json={"id": 5})
        assert mgr.add_submission_policy(COLL, GRP, name="extra", start_date="2027-01-01")["id"] == 5
        req = http.calls[-1].request
        assert req.params == {"resource": COLL, "group": GRP}
        body = _json.loads(req.body)
        assert body["policyType"] == "TYPE_SUBMISSION" and body["action"] == "ADD" and body["startDate"] == "2027-01-01"

        http.add(_responses.DELETE, api_url("/authz/resourcepolicies/5"), status=204)
        assert mgr.delete_submission_policy(5) is True
