"""
Tests for workflow.py: workflow items, pool / claimed tasks, review flows,
workflow configuration and supervision orders.
"""

import json
from urllib.parse import parse_qsl, urlparse

import pytest
import responses

from dspace_api.exceptions import DSpaceError, ServerError
from dspace_api.metadata import MetadataPatch
from dspace_api.workflow import WorkflowError, WorkflowManager, submit_param

API = "http://localhost:8080/server/api"
ITEM = "11111111-1111-1111-1111-111111111111"
ME = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
OTHER = "dddddddd-dddd-dddd-dddd-dddddddddddd"
GROUP = "99999999-9999-9999-9999-999999999999"
COLL = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture
def mgr(http_auth):
    return WorkflowManager(http_auth)


def last(http, method=None):
    return [c.request for c in http.calls if method is None or c.request.method == method][-1]


def params(request):
    return parse_qsl(urlparse(request.url).query)


def task(task_id, kind="pooltask", action="reviewaction", title="My thesis"):
    return {"id": task_id, "type": kind, "step": "reviewstep", "action": action,
            "_embedded": {"workflowitem": {"id": 7, "_embedded": {"item": {
                "uuid": ITEM, "metadata": {"dc.title": [{"value": title}]}}}}}}


@pytest.fixture
def me(http, api_url):
    http.add(responses.GET, api_url("/authn/status"),
             json={"authenticated": True, "_embedded": {"eperson": {"uuid": ME}}})
    return ME


class TestHelpers:
    def test_submit_param(self):
        assert submit_param("approve") == "submit_approve"
        assert submit_param("submit_score") == "submit_score"

    def test_refs(self, mgr):
        assert mgr._id_of({"id": 3}) == "3" and mgr._id_of({"uuid": "u"}) == "u" and mgr._id_of(4) == "4"
        with pytest.raises(ValueError):
            mgr._id_of({"name": "x"})
        assert mgr._uuid({"uuid": ITEM}) == ITEM
        for bad in (5, "not-a-uuid"):
            with pytest.raises(ValueError):
                mgr._uuid(bad, "item")

    def test_user_uuid(self, mgr, me):
        assert mgr._user_uuid(None) == ME
        assert mgr._user_uuid({"uuid": OTHER}) == OTHER
        assert mgr.epersons is mgr.epersons

    def test_user_uuid_not_logged_in(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authn/status"), json={"authenticated": False})
        with pytest.raises(WorkflowError):
            mgr._user_uuid(None)


# ---------------------------------------------------------------------------
# Workflow items
# ---------------------------------------------------------------------------

class TestWorkflowItems:
    def test_list_get_links(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/workflow/workflowitems"), json=hal_page("workflowitems", [{"id": 7}]))
        http.add(responses.GET, api_url("/workflow/workflowitems/7"), json={"id": 7})
        http.add(responses.GET, api_url("/workflow/workflowitems/7/step"), json={"id": "reviewstep"})
        http.add(responses.GET, api_url("/workflow/workflowitems/7/item"), json={"uuid": ITEM})
        assert list(mgr.iter_workflow_items(page_size=5, embed="item")) == [{"id": 7}]
        assert mgr.get_workflow_item({"id": 7})["id"] == 7
        assert mgr.get_workflow_item_link(7, "step")["id"] == "reviewstep"
        mgr.get_workflow_item_link(7, "item", all_languages=True)
        assert ("projection", "allLanguages") in params(last(http))
        with pytest.raises(ValueError):
            mgr.get_workflow_item_link(7, "owner")

    def test_searches(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/workflow/workflowitems/search/findBySubmitter"),
                 json=hal_page("workflowitems", [{"id": 7}]))
        url = api_url("/workflow/workflowitems/search/item")
        http.add(responses.GET, url, json={"id": 7})
        assert mgr.find_by_submitter(ME) == [{"id": 7}]
        assert ("uuid", ME) in params(last(http))
        assert mgr.find_by_item(ITEM)["id"] == 7
        http.replace(responses.GET, url, status=204)
        assert mgr.find_by_item({"uuid": ITEM}) is None

    def test_return_and_delete(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url("/workflow/workflowitems/7"), status=204)
        assert mgr.return_to_workspace(7) is True
        assert "expunge" not in dict(params(last(http)))
        assert mgr.delete_workflow_item(7) is True
        assert params(last(http)) == [("expunge", "true")]

    def test_patch(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url("/workflow/workflowitems/7"), json={"id": 7})
        mgr.patch_section_metadata(7, "traditionalpageone", {"dc.title": "New"})
        request = last(http)
        assert request.headers["Content-Type"] == "application/json-patch+json"
        assert json.loads(request.body) == [{"op": "add", "path": "/sections/traditionalpageone/dc.title",
                                             "value": [{"value": "New"}]}]
        mgr.patch_section_metadata(7, "p", MetadataPatch.for_section("p").remove("dc.subject"))
        assert json.loads(last(http).body) == [{"op": "remove", "path": "/sections/p/dc.subject"}]
        with pytest.raises(ValueError):
            mgr.patch_workflow_item(7, [])


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

class TestTasks:
    def test_pool_tasks(self, mgr, http, api_url, hal_page, me):
        for method in ("findByUser", "findAllByItem"):
            http.add(responses.GET, api_url(f"/workflow/pooltasks/search/{method}"),
                     json=hal_page("pooltasks", [task(1)]))
        http.add(responses.GET, api_url("/workflow/pooltasks/1"), json=task(1))
        http.add(responses.GET, api_url("/workflow/pooltasks/1/group"), json={"uuid": GROUP})
        assert [t["id"] for t in mgr.pool_tasks_for_user()] == [1]
        p = params(last(http))
        assert ("uuid", ME) in p and ("embed", "workflowitem") in p and ("embed", "workflowitem/item") in p
        assert mgr.pool_tasks_for_item(ITEM)[0]["id"] == 1
        assert mgr.get_pool_task(1, embed="workflowitem")["id"] == 1
        assert mgr.get_pool_task_link(1, "group")["uuid"] == GROUP
        with pytest.raises(ValueError):
            mgr.get_pool_task_link(1, "owner")

    def test_find_pool_task(self, mgr, http, api_url):
        url = api_url("/workflow/pooltasks/search/findByItem")
        http.add(responses.GET, url, json=task(1))
        assert mgr.find_pool_task(ITEM)["id"] == 1
        http.replace(responses.GET, url, status=204)
        assert mgr.find_pool_task(ITEM) is None

    def test_claim(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/workflow/claimedtasks"), json={"id": 50, "type": "claimedtask"},
                 status=201)
        assert mgr.claim({"id": 1})["id"] == 50
        request = last(http)
        assert request.headers["Content-Type"] == "text/uri-list" and request.body == f"{API}/workflow/pooltasks/1"

    def test_claimed_tasks(self, mgr, http, api_url, hal_page):
        for method in ("findByUser", "findAllByItem"):
            http.add(responses.GET, api_url(f"/workflow/claimedtasks/search/{method}"),
                     json=hal_page("claimedtasks", [task(50, "claimedtask")]))
        url = api_url("/workflow/claimedtasks/search/findByItem")
        http.add(responses.GET, url, json=task(50, "claimedtask"))
        http.add(responses.GET, api_url("/workflow/claimedtasks/50"), json=task(50, "claimedtask"))
        http.add(responses.GET, api_url("/workflow/claimedtasks/50/owner"), json={"uuid": ME})
        assert mgr.claimed_tasks_for_user(OTHER)[0]["id"] == 50
        assert ("uuid", OTHER) in params(last(http))
        assert mgr.claimed_tasks_for_item(ITEM)[0]["id"] == 50
        assert mgr.find_claimed_task(ITEM)["id"] == 50
        assert mgr.get_claimed_task(50)["id"] == 50
        assert mgr.get_claimed_task_link(50, "owner")["uuid"] == ME
        with pytest.raises(ValueError):
            mgr.get_claimed_task_link(50, "group")
        http.replace(responses.GET, url, status=204)
        assert mgr.find_claimed_task(ITEM) is None

    def test_task_options(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/config/workflowactions/reviewaction"),
                 json={"id": "reviewaction", "options": ["approve", "reject"]})
        http.add(responses.GET, api_url("/workflow/claimedtasks/50/action"), json={"options": ["approve"]})
        http.add(responses.GET, api_url("/workflow/pooltasks/1/action"), json={"options": ["claim"]})
        assert mgr.task_options(task(1)) == ["approve", "reject"]
        calls = len(http.calls)
        mgr.task_options(task(2))
        assert len(http.calls) == calls                                      # action config cached
        assert mgr.task_options(50) == ["approve"]                           # id -> claimed task link
        assert mgr.task_options({"id": 50, "type": "claimedtask"}) == ["approve"]
        assert mgr.task_options({"id": 1, "type": "pooltask"}) == ["claim"]
        http.replace(responses.GET, api_url("/workflow/pooltasks/1/action"), status=204)
        assert mgr.task_options({"id": 1, "type": "pooltask"}) == []


class TestAllLinks:
    """Every documented linked entity can be read (and 204 means: no such link)."""

    @pytest.mark.parametrize("base, getter, links", [
        ("/workflow/workflowitems/7", "get_workflow_item_link", ("item", "collection", "submissionDefinition", "step")),
        ("/workflow/pooltasks/7", "get_pool_task_link", ("workflowitem", "eperson", "group", "step", "action")),
        ("/workflow/claimedtasks/7", "get_claimed_task_link", ("workflowitem", "owner", "step", "action")),
    ])
    def test_links(self, mgr, http, api_url, base, getter, links):
        for link in links:
            http.add(responses.GET, api_url(f"{base}/{link}"), json={"id": link})
            assert getattr(mgr, getter)(7, link) == {"id": link}
        http.replace(responses.GET, api_url(f"{base}/{links[0]}"), status=204)
        assert getattr(mgr, getter)(7, links[0]) is None


class TestPerform:
    URL = "/workflow/claimedtasks/50"

    def form(self, http):
        return parse_qsl(last(http, "POST").body)

    def test_approve_reject(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=204)
        assert mgr.approve(50) is True
        request = last(http)
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert self.form(http) == [("submit_approve", "true")]
        mgr.reject({"id": 50}, "Please add an abstract")
        assert self.form(http) == [("submit_reject", "true"), ("reason", "Please add an abstract")]
        with pytest.raises(ValueError):
            mgr.reject(50, "  ")

    def test_advanced_actions(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=204)
        mgr.select_reviewers(50, [ME, {"uuid": OTHER}])
        assert self.form(http) == [("submit_select_reviewer", "true"), ("eperson", ME), ("eperson", OTHER)]
        mgr.select_reviewers(50, [OTHER])
        assert self.form(http) == [("submit_select_reviewer", "true"), ("eperson", OTHER),
                                   (f"submit_select_reviewer_{OTHER}", "true")]
        with pytest.raises(ValueError):
            mgr.select_reviewers(50, [])
        mgr.score(50, "4", review="Solid")
        assert self.form(http) == [("submit_score", "true"), ("score", "4"), ("review", "Solid"),
                                   ("description", "Solid")]
        mgr.score(50, 2)
        assert self.form(http) == [("submit_score", "true"), ("score", "2")]
        mgr.perform(50, "submit_decline_task")
        assert self.form(http) == [("submit_decline_task", "true")]

    @pytest.mark.parametrize("status", [403, 404, 422])
    def test_review_errors(self, mgr, http, api_url, status):
        http.add(responses.POST, api_url(self.URL), status=status, body="no")
        with pytest.raises(WorkflowError) as info:
            mgr.approve(50)
        assert info.value.status_code == status

    def test_other_errors(self, mgr, http, api_url):
        http.add(responses.POST, api_url(self.URL), status=500)
        with pytest.raises(ServerError):
            mgr.approve(50)

    def test_unclaim(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url(self.URL), status=204)
        assert mgr.unclaim(50) is True


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------

class TestFlows:
    @pytest.fixture
    def server(self, http, api_url):
        http.add(responses.GET, api_url("/workflow/claimedtasks/search/findByItem"), status=204)
        http.add(responses.GET, api_url("/workflow/pooltasks/search/findByItem"), json=task(1))
        http.add(responses.POST, api_url("/workflow/claimedtasks"), json={"id": 50, "type": "claimedtask"},
                 status=201)
        http.add(responses.POST, api_url("/workflow/claimedtasks/50"), status=204)
        return http

    def test_claim_for_item(self, mgr, server, api_url):
        assert mgr.claim_for_item(ITEM)["id"] == 50
        server.replace(responses.GET, api_url("/workflow/claimedtasks/search/findByItem"), json={"id": 60})
        assert mgr.claim_for_item(ITEM)["id"] == 60                  # already claimed -> no new claim

    def test_claim_for_item_without_task(self, mgr, server, api_url):
        server.replace(responses.GET, api_url("/workflow/pooltasks/search/findByItem"), status=204)
        with pytest.raises(WorkflowError, match="not a member"):
            mgr.claim_for_item(ITEM)

    def test_review(self, mgr, server, api_url):
        server.add(responses.GET, api_url("/workflow/workflowitems/search/item"), status=204)
        result = mgr.review(ITEM, "approve")
        assert result["workflow_item"] is None and result["task"]["id"] == 50
        assert parse_qsl(last(server, "POST").body) == [("submit_approve", "true")]
        mgr.review(ITEM, "reject", reason="Missing abstract")
        assert ("reason", "Missing abstract") in parse_qsl(last(server, "POST").body)
        with pytest.raises(ValueError):
            mgr.review(ITEM, "reject")
        mgr.review(ITEM, "score", score=3)
        assert parse_qsl(last(server, "POST").body) == [("submit_score", "true"), ("score", "3")]

    def test_approve_all_steps(self, mgr, server, api_url):
        url = api_url("/workflow/workflowitems/search/item")
        server.add(responses.GET, url, json={"id": 7})
        server.add(responses.GET, url, json={"id": 7})
        server.add(responses.GET, url, status=204)
        assert mgr.approve_all_steps(ITEM) == 2

    def test_approve_all_steps_limit(self, mgr, server, api_url):
        server.add(responses.GET, api_url("/workflow/workflowitems/search/item"), json={"id": 7})
        with pytest.raises(WorkflowError, match="after 1 approvals"):
            mgr.approve_all_steps(ITEM, max_steps=1)

    def test_pending_tasks(self, mgr, http, api_url, hal_page, me):
        no_title = {"id": 3, "type": "pooltask", "step": "editstep", "action": None,
                    "_embedded": {"workflowitem": {"id": 8, "_embedded": {"item": {"uuid": "u2", "name": "Untitled"}}}}}
        http.add(responses.GET, api_url("/workflow/claimedtasks/search/findByUser"),
                 json=hal_page("claimedtasks", [task(50, "claimedtask")]))
        http.add(responses.GET, api_url("/workflow/pooltasks/search/findByUser"),
                 json=hal_page("pooltasks", [no_title]))
        http.add(responses.GET, api_url("/config/workflowactions/reviewaction"),
                 json={"options": ["approve", "reject"]})
        pending = mgr.pending_tasks()
        assert pending == [
            {"kind": "claimed", "task_id": 50, "step": "reviewstep", "action": "reviewaction",
             "options": ["approve", "reject"], "workflow_item_id": 7, "item_uuid": ITEM, "title": "My thesis"},
            {"kind": "pool", "task_id": 3, "step": "editstep", "action": None, "options": [],
             "workflow_item_id": 8, "item_uuid": "u2", "title": "Untitled"},
        ]
        assert [t["kind"] for t in mgr.pending_tasks(ME, claimed=False)] == ["pool"]
        assert [t["kind"] for t in mgr.pending_tasks(ME, pool=False)] == ["claimed"]


# ---------------------------------------------------------------------------
# Configuration and supervision
# ---------------------------------------------------------------------------

class TestConfiguration:
    def test_definitions(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/config/workflowdefinitions"),
                 json=hal_page("workflowdefinitions", [{"name": "default", "isDefault": True}]))
        http.add(responses.GET, api_url("/config/workflowdefinitions/default"), json={"name": "default"})
        http.add(responses.GET, api_url("/config/workflowdefinitions/search/findByCollection"),
                 json={"name": "default"})
        http.add(responses.GET, api_url("/config/workflowdefinitions/default/collections"),
                 json=hal_page("collections", [{"uuid": COLL}]))
        http.add(responses.GET, api_url("/config/workflowdefinitions/default/steps"),
                 json=hal_page("steps", [{"id": "reviewstep"}, {"id": "editstep"}]))
        http.add(responses.GET, api_url("/config/workflowsteps/editstep"), json={"id": "editstep"})
        assert mgr.get_workflow_definitions()[0]["isDefault"] is True
        assert mgr.get_workflow_definition("default")["name"] == "default"
        assert mgr.definition_for_collection({"uuid": COLL})["name"] == "default"
        assert ("uuid", COLL) in params(last(http))
        assert mgr.definition_collections("default") == [{"uuid": COLL}]
        assert [s["id"] for s in mgr.definition_steps("default")] == ["reviewstep", "editstep"]
        assert mgr.get_workflow_step("editstep")["id"] == "editstep"


class TestSupervision:
    def test_list_get_by_item(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/supervisionorders"),
                 json=hal_page("supervisionorders", [{"id": 42}]))
        http.add(responses.GET, api_url("/core/supervisionorders/42"), json={"id": 42})
        http.add(responses.GET, api_url("/core/supervisionorders/search/byItem"),
                 json=hal_page("supervisionorders", [{"id": 42}]))
        assert list(mgr.iter_supervision_orders()) == [{"id": 42}]
        assert ("embed", "item") in params(last(http)) and ("embed", "group") in params(last(http))
        list(mgr.iter_supervision_orders(embed=None))
        assert "embed" not in dict(params(last(http)))
        assert mgr.get_supervision_order(42, embed="group")["id"] == 42
        assert mgr.supervision_orders_for_item(ITEM)[0]["id"] == 42
        assert ("uuid", ITEM) in params(last(http))

    def test_create_and_delete(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/core/supervisionorders"), json={"id": 43}, status=201)
        http.add(responses.DELETE, api_url("/core/supervisionorders/43"), status=204)
        assert mgr.create_supervision_order({"uuid": ITEM}, GROUP, "editor")["id"] == 43
        assert dict(params(last(http))) == {"uuid": ITEM, "group": GROUP, "type": "EDITOR"}
        with pytest.raises(ValueError):
            mgr.create_supervision_order(ITEM, GROUP, "owner")
        assert mgr.delete_supervision_order(43) is True

    def test_create_conflict(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/core/supervisionorders"), status=422)
        with pytest.raises(DSpaceError):
            mgr.create_supervision_order(ITEM, GROUP)
