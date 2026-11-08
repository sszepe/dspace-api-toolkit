"""
Tests for versions.py (item versioning)
"""

import json

import pytest
import responses

from dspace_api.exceptions import DSpaceError, NotFoundError
from dspace_api.versions import VersionManager

API = "http://localhost:8080/server/api"
ITEM = "11111111-1111-1111-1111-111111111111"
NEW_ITEM = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def mgr(http_auth):
    return VersionManager(http_auth)


def last(http, method=None):
    return [c.request for c in http.calls if method is None or c.request.method == method][-1]


def versions_page(*versions):
    return {"_embedded": {"versions": list(versions)}, "page": {"totalPages": 1}}


@pytest.fixture
def history(http, api_url):
    """ITEM is version 1 (id 101) in history 5; version 2 (id 102) is newer."""
    http.add(responses.GET, api_url(f"/core/items/{ITEM}/version"), json={"id": 101, "version": 1})
    http.add(responses.GET, api_url("/versioning/versions/101/versionhistory"), json={"id": 5})
    http.add(responses.GET, api_url("/versioning/versionhistories/5/versions"),
             json=versions_page({"id": 102, "version": 2}, {"id": 101, "version": 1}))
    return http


class TestVersions:
    def test_get(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/versioning/versions/101"), json={"id": 101})
        http.add(responses.GET, api_url("/versioning/versions/101/item"), json={"uuid": ITEM})
        assert mgr.get_version(101, embed="item")["id"] == 101
        assert last(http).params == {"embed": "item"}
        assert mgr.get_version_item(101)["uuid"] == ITEM

    def test_item_version(self, mgr, http, api_url):
        url = api_url(f"/core/items/{ITEM}/version")
        http.add(responses.GET, url, json={"id": 101})
        assert mgr.get_item_version(ITEM)["id"] == 101
        http.replace(responses.GET, url, status=204)
        assert mgr.get_item_version(ITEM) is None

    def test_create(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/versioning/versions"), json={"id": 102, "version": 2}, status=201)
        assert mgr.create_version(ITEM, "Typo fixes")["id"] == 102
        request = last(http)
        assert request.headers["Content-Type"] == "text/uri-list"
        assert request.body == f"{API}/core/items/{ITEM}"
        assert request.params == {"summary": "Typo fixes"}
        mgr.create_version(ITEM)
        assert last(http).params == {}

    def test_create_with_existing_draft(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/versioning/versions"), status=422)
        with pytest.raises(DSpaceError):
            mgr.create_version(ITEM)

    def test_summary(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url("/versioning/versions/102"), json={"id": 102})
        mgr.set_summary(102, "New")
        assert json.loads(last(http).body) == [{"op": "add", "path": "/summary", "value": "New"}]
        mgr.set_summary(102, None)
        assert json.loads(last(http).body) == [{"op": "remove", "path": "/summary"}]


class TestHistories:
    def test_history_endpoints(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/versioning/versionhistories/5"), json={"id": 5, "draftVersion": True})
        draft_url = api_url("/versioning/versionhistories/5/draftVersion")
        http.add(responses.GET, draft_url, json={"id": 42, "type": "workspaceitem"})
        assert mgr.get_history(5)["draftVersion"] is True
        assert mgr.get_draft_version(5)["id"] == 42
        http.replace(responses.GET, draft_url, status=204)
        assert mgr.get_draft_version(5) is None

    def test_item_history(self, mgr, history):
        assert mgr.history_of_item(ITEM) == {"id": 5}
        assert [v["id"] for v in mgr.list_item_versions(ITEM)] == [102, 101]
        assert last(history).params["embed"] == "item"
        assert mgr.latest_version(ITEM)["id"] == 102
        assert mgr.is_latest(ITEM) is False

    def test_is_latest(self, mgr, history, api_url):
        history.replace(responses.GET, api_url("/versioning/versionhistories/5/versions"),
                        json=versions_page({"id": 101}))
        assert mgr.is_latest(ITEM) is True
        history.replace(responses.GET, api_url("/versioning/versionhistories/5/versions"), json=versions_page())
        assert mgr.is_latest(ITEM) is True

    def test_unversioned_item(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"/core/items/{ITEM}/version"), status=204)
        assert mgr.history_of_item(ITEM) is None
        assert mgr.list_item_versions(ITEM) == []
        assert mgr.latest_version(ITEM) is None
        assert mgr.is_latest(ITEM) is True
        assert mgr.discard_draft(ITEM) is False


class TestFlows:
    @pytest.fixture
    def server(self, http, api_url, hal_page):
        http.add(responses.POST, api_url("/versioning/versions"), json={"id": 102}, status=201)
        http.add(responses.GET, api_url("/versioning/versions/102/item"), json={"uuid": NEW_ITEM})
        http.add(responses.GET, api_url("/submission/workspaceitems/search/item"),
                 json={"id": 42, "sections": {"page": {}}})
        http.add(responses.GET, api_url("/submission/workspaceitems/42"), json={"id": 42, "sections": {"page": {}}})
        http.add(responses.GET, api_url("/submission/workspaceitems/42/submissionDefinition"), json={"name": "d"})
        http.add(responses.GET, api_url("/config/submissiondefinitions/d"),
                 json={"name": "d", "_embedded": {"sections": hal_page(
                     "sections", [{"id": "page", "sectionType": "submission-form"}])}})
        http.add(responses.GET, api_url("/config/submissionsections/page"), json={})
        http.add(responses.GET, api_url("/config/submissionforms/page"),
                 json={"rows": [{"fields": [{"selectableMetadata": [{"metadata": "dc.title"}]}]}]})
        http.add(responses.PATCH, api_url("/submission/workspaceitems/42"), json={"id": 42, "errors": []})
        http.add(responses.POST, api_url("/workflow/workflowitems"), json={"id": 7}, status=201)
        return http

    def test_new_version(self, mgr, server):
        result = mgr.new_version(ITEM, "Changed title", metadata={"dc.title": "New"})
        assert result["item_uuid"] == NEW_ITEM and result["workflow_item"] == {"id": 7}
        assert json.loads(last(server, "PATCH").body)[0]["path"] == "/sections/page/dc.title"
        assert mgr.workspace is mgr.workspace  # cached

    def test_new_version_draft_only(self, mgr, server):
        result = mgr.new_version(ITEM, deposit=False)
        assert result["workflow_item"] is None and result["workspace_item"]["id"] == 42
        assert not [c for c in server.calls if c.request.method == "PATCH"]

    def test_new_version_without_workspace_item(self, mgr, server, api_url):
        server.replace(responses.GET, api_url("/submission/workspaceitems/search/item"), status=204)
        with pytest.raises(DSpaceError, match="no workspace item"):
            mgr.new_version(ITEM)

    def test_discard_draft(self, mgr, history, api_url):
        draft_url = api_url("/versioning/versionhistories/5/draftVersion")
        history.add(responses.GET, draft_url, json={"id": 42, "type": "workspaceitem"})
        history.add(responses.DELETE, api_url("/submission/workspaceitems/42"), status=204)
        assert mgr.discard_draft(ITEM) is True
        history.replace(responses.GET, draft_url, json={"id": 43, "type": "workflowitem"})
        with pytest.raises(DSpaceError, match="workflowitem"):
            mgr.discard_draft(ITEM)
        history.replace(responses.GET, draft_url, status=204)
        assert mgr.discard_draft(ITEM) is False

    def test_errors_propagate(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"/core/items/{ITEM}/version"), status=404)
        with pytest.raises(NotFoundError):
            mgr.list_item_versions(ITEM)
