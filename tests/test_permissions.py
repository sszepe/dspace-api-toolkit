"""
Tests for DSpace permissions module (resource policies, groups, bulk access control)
"""

import json
from datetime import date, datetime
from unittest.mock import Mock, patch

import pytest
import requests
import responses

from dspace_api.bundles import BundleManager
from dspace_api.exceptions import AuthorizationError, DSpaceError, NotFoundError, ServerError
from dspace_api.permissions import (
    ANONYMOUS, POLICY_ROW_FIELDS, Actions, PermissionManager, PolicyTypes, _as_list, _fmt_date,
)

pytestmark = pytest.mark.permissions

ANON = {"uuid": "11111111-1111-1111-1111-111111111111", "name": "Anonymous"}
ADMIN = {"uuid": "22222222-2222-2222-2222-222222222222", "name": "Administrator"}
POLICIES = "/authz/resourcepolicies"


def policy(pid, action="READ", group=ANON, start=None, end=None, ptype=PolicyTypes.INHERITED, embed=True):
    p = {"id": pid, "action": action, "policyType": ptype, "startDate": start, "endDate": end,
         "name": None, "description": None, "type": "resourcepolicy"}
    if embed:
        p["_embedded"] = {"group": group, "eperson": None}
    return p


@pytest.fixture
def mgr(http_auth):
    return PermissionManager(http_auth)


@pytest.fixture
def resource_policies(http, api_url, hal_page):
    """Register policies per resource uuid: resource_policies({"it1": [...]})"""
    def register(by_uuid):
        def cb(request):
            uuid = request.params["uuid"]
            return 200, {}, json.dumps(hal_page("resourcepolicies", by_uuid.get(uuid, [])))
        http.add_callback(responses.GET, api_url(f"{POLICIES}/search/resource"), callback=cb)
    return register


def last(http):
    return http.calls[-1].request


def embedded_page(key, objects, total=None):
    total = len(objects) if total is None else total
    return {"_embedded": {key: objects}, "page": {"totalElements": total, "totalPages": 1, "number": 0}}


def embedded_item(uuid, bundles, name=None):
    """Item JSON as GET ...?embed=bundles&embed=bundles/bitstreams returns it"""
    item = {"uuid": uuid, "_embedded": {"bundles": embedded_page("bundles", [
        dict(bundle, _embedded={"bitstreams": embedded_page("bitstreams", bitstreams)})
        for bundle, bitstreams in bundles])}}
    if name is not None:
        item["name"] = name
    return item


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    @pytest.mark.parametrize("value,expected", [
        (None, None), ("", None), (date(2026, 1, 2), "2026-01-02"),
        (datetime(2026, 1, 2, 13, 5), "2026-01-02"), ("2026-01-02T00:00:00.000+0000", "2026-01-02"),
        (" 2026-01-02 ", "2026-01-02"),
    ])
    def test_fmt_date(self, value, expected):
        assert _fmt_date(value) == expected

    def test_as_list(self):
        assert _as_list(None) is None
        assert _as_list("READ") == ["READ"]
        assert _as_list(("READ", "WRITE")) == ["READ", "WRITE"]

    def test_constants(self):
        assert "DEFAULT_ITEM_READ" in Actions.ALL
        assert PolicyTypes.CUSTOM in PolicyTypes.ALL
        assert "status" in POLICY_ROW_FIELDS


# ---------------------------------------------------------------------------
# Request plumbing
# ---------------------------------------------------------------------------

class TestRequestPlumbing:
    def test_init(self, mock_auth):
        m = PermissionManager(mock_auth)
        assert m.auth is mock_auth and m.config is mock_auth.config and m.session is mock_auth.session

    def test_bundles_property_is_lazy_and_cached(self, mgr):
        assert mgr._bundle_manager is None
        b = mgr.bundles
        assert isinstance(b, BundleManager) and mgr.bundles is b

    def test_csrf_retry_on_403_for_writes(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url(f"{POLICIES}/1"), status=403, headers={"DSPACE-XSRF-TOKEN": "fresh"})
        http.add(responses.DELETE, api_url(f"{POLICIES}/1"), status=204)
        assert mgr.delete_policy(1) is True
        assert len(http.calls) == 2
        assert http.calls[1].request.headers["X-XSRF-Token"] == "fresh"

    def test_no_retry_for_get_or_plain_403(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"{POLICIES}/1"), status=403, headers={"DSPACE-XSRF-TOKEN": "t"})
        with pytest.raises(AuthorizationError):
            mgr.get_policy(1)
        http.add(responses.DELETE, api_url(f"{POLICIES}/2"), status=403)
        with pytest.raises(AuthorizationError):
            mgr.delete_policy(2)
        assert len(http.calls) == 2

    def test_auth_without_update_token(self, dspace_config, http, api_url):
        fake = Mock(spec=["session", "config"])
        fake.session, fake.config = requests.Session(), dspace_config
        http.add(responses.GET, api_url("/eperson/groups/g"), json=ANON)
        assert PermissionManager(fake).get_group("g") == ANON

    def test_pagination_variants(self, mgr, http, api_url, hal_page):
        url = api_url(f"{POLICIES}/search/group")
        http.add(responses.GET, url, json=hal_page("resourcepolicies", [policy(1)], total_pages=2))
        http.add(responses.GET, url, json=hal_page("resourcepolicies", [policy(2)], total_pages=2))
        assert len(mgr.get_group_policies("g")) == 2
        # no page block: stops on a short page, continues on a full one
        mgr.DEFAULT_PAGE_SIZE = 1
        http.replace(responses.GET, url, json={"_embedded": {"resourcepolicies": [policy(1)]}})
        http.add(responses.GET, url, json={"_embedded": {"resourcepolicies": []}})
        assert len(mgr.get_group_policies("g")) == 1
        mgr.DEFAULT_PAGE_SIZE = 100
        http.replace(responses.GET, url, json={"_embedded": {"resourcepolicies": [policy(1)]}})
        assert len(mgr.get_group_policies("g")) == 1


# ---------------------------------------------------------------------------
# Reading policies
# ---------------------------------------------------------------------------

class TestReadPolicies:
    def test_get_policy_embeds(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"{POLICIES}/5"), json=policy(5))
        mgr.get_policy(5)
        assert last(http).url.endswith("embed=group&embed=eperson")

    def test_get_resource_policies_with_and_without_action(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"{POLICIES}/search/resource"), json=hal_page("resourcepolicies", [policy(1)]))
        mgr.get_resource_policies("r1")
        assert "action" not in last(http).params
        mgr.get_resource_policies("r1", action="READ")
        assert last(http).params["action"] == "READ" and last(http).params["uuid"] == "r1"

    def test_group_and_eperson_policies_with_resource_filter(self, mgr, http, api_url, hal_page):
        for kind in ("group", "eperson"):
            http.add(responses.GET, api_url(f"{POLICIES}/search/{kind}"), json=hal_page("resourcepolicies", []))
        mgr.get_group_policies("g", resource_uuid="r")
        assert last(http).params == {"uuid": "g", "resource": "r", "page": "0", "size": "100"}
        mgr.get_eperson_policies("e")
        assert "resource" not in last(http).params
        mgr.get_eperson_policies("e", resource_uuid="r")
        assert last(http).params["resource"] == "r"

    @pytest.mark.parametrize("sub,getter", [("group", "get_policy_group"), ("eperson", "get_policy_eperson")])
    def test_linked_group_and_eperson(self, mgr, http, api_url, sub, getter):
        http.add(responses.GET, api_url(f"{POLICIES}/5/{sub}"), json={"uuid": "x"})
        assert getattr(mgr, getter)(5) == {"uuid": "x"}
        http.replace(responses.GET, api_url(f"{POLICIES}/5/{sub}"), status=204)
        assert getattr(mgr, getter)(5) is None

    def test_get_policy_resource(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"{POLICIES}/5/resource"), json={"uuid": "it1"})
        assert mgr.get_policy_resource(5) == {"uuid": "it1"}


# ---------------------------------------------------------------------------
# Writing policies
# ---------------------------------------------------------------------------

class TestWritePolicies:
    def test_create_policy_for_group_name(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/groups/search/byMetadata"), json=hal_page("groups", [ANON]))
        http.add(responses.POST, api_url(POLICIES), status=201, json={"id": 77})
        out = mgr.create_policy("r1", group="anonymous", start_date=date(2027, 1, 1), name="embargo")
        assert out["id"] == 77
        req = last(http)
        assert req.params == {"resource": "r1", "group": ANON["uuid"]}
        body = json.loads(req.body)
        assert body == {"name": "embargo", "description": None, "policyType": "TYPE_CUSTOM", "action": "READ",
                        "startDate": "2027-01-01", "endDate": None, "type": "resourcepolicy"}

    def test_create_policy_for_eperson_with_null_policy_type(self, mgr, http, api_url):
        http.add(responses.POST, api_url(POLICIES), status=200, json={"id": 78})
        mgr.create_policy("r1", action="WRITE", eperson_uuid="e1", policy_type=None)
        assert last(http).params == {"resource": "r1", "eperson": "e1"}
        assert json.loads(last(http).body)["policyType"] is None

    @pytest.mark.parametrize("kwargs,match", [
        ({}, "exactly one"),
        ({"group": "g", "eperson_uuid": "e"}, "exactly one"),
        ({"group": "g", "action": "FLY"}, "Unknown action"),
        ({"group": "g", "policy_type": "TYPE_X"}, "Unknown policy_type"),
    ])
    def test_create_policy_validation(self, mgr, kwargs, match):
        with pytest.raises(ValueError, match=match):
            mgr.create_policy("r1", **kwargs)

    def test_patch_policy(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url(f"{POLICIES}/5"), json={"id": 5})
        mgr.patch_policy(5, [{"op": "add", "path": "/name", "value": "n"}])
        assert last(http).headers["Content-Type"] == "application/json"

    def test_update_policy_builds_add_and_remove_ops(self, mgr, http, api_url):
        current = policy(5, start="2020-01-01", embed=False)
        http.add(responses.GET, api_url(f"{POLICIES}/5"), json=current)
        http.add(responses.PATCH, api_url(f"{POLICIES}/5"), json={"id": 5})
        mgr.update_policy(5, name="n", description=None, start_date=None, end_date=datetime(2030, 5, 6))
        assert json.loads(last(http).body) == [
            {"op": "add", "path": "/name", "value": "n"},
            # description is already None -> no 'remove' (DSpace would reject it)
            {"op": "remove", "path": "/startDate"},
            {"op": "add", "path": "/endDate", "value": "2030-05-06"},
        ]

    def test_update_policy_without_changes_returns_current(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"{POLICIES}/5"), json=policy(5, embed=False))
        assert mgr.update_policy(5, end_date=None)["id"] == 5
        assert [c.request.method for c in http.calls] == ["GET"]

    def test_delete_policy_missing(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url(f"{POLICIES}/5"), status=404)
        assert mgr.delete_policy(5, missing_ok=True) is False
        with pytest.raises(NotFoundError):
            mgr.delete_policy(5)

    def test_set_policy_group_and_eperson(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"/eperson/groups/{ADMIN['uuid']}"), json=ADMIN)
        http.add(responses.PUT, api_url(f"{POLICIES}/5/group"), status=204)
        assert mgr.set_policy_group(5, ADMIN["uuid"]) is True
        assert last(http).headers["Content-Type"] == "text/uri-list"
        assert last(http).body == api_url(f"/eperson/groups/{ADMIN['uuid']}")

        http.add(responses.PUT, api_url(f"{POLICIES}/5/eperson"), status=204)
        assert mgr.set_policy_eperson(5, "e1") is True
        assert last(http).body == api_url("/eperson/epersons/e1")


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

class TestGroups:
    def test_find_group_by_name_exact(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/groups/search/byMetadata"),
                 json=hal_page("groups", [{"uuid": "x", "name": "Anonymous Readers"}, ANON]))
        assert mgr.find_group_by_name("ANONYMOUS") == ANON
        assert mgr.find_group_by_name("Nobody") is None

    def test_resolve_group_by_uuid_and_cache(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"/eperson/groups/{ADMIN['uuid']}"), json=ADMIN)
        assert mgr.resolve_group(ADMIN["uuid"]) == ADMIN
        assert mgr.resolve_group(ADMIN["uuid"].upper()) == ADMIN
        assert len(http.calls) == 1

    def test_resolve_group_not_found(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/groups/search/byMetadata"), json=hal_page("groups", []))
        with pytest.raises(NotFoundError, match="not found"):
            mgr.resolve_group("Ghosts")


# ---------------------------------------------------------------------------
# Filtering and access checks
# ---------------------------------------------------------------------------

class TestFiltering:
    def test_policy_group_embedded_or_fetched(self, mgr, http, api_url):
        assert mgr.policy_group(policy(1)) == ANON
        assert mgr.policy_group(policy(1, group=None)) is None     # eperson policy, embedded null
        http.add(responses.GET, api_url(f"{POLICIES}/1/group"), json=ADMIN)
        assert mgr.policy_group(policy(1, embed=False)) == ADMIN

    def test_filter_policies(self, mgr):
        pols = [policy(1), policy(2, action="WRITE"), policy(3, group=ADMIN),
                policy(4, ptype=PolicyTypes.CUSTOM), policy(5, group=None)]
        ids = lambda ps: [p["id"] for p in ps]
        assert ids(mgr.filter_policies(pols)) == [1, 2, 3, 4, 5]
        assert ids(mgr.filter_policies(pols, group="anonymous")) == [1, 2, 4]
        assert ids(mgr.filter_policies(pols, group=ADMIN["uuid"])) == [3]
        assert ids(mgr.filter_policies(pols, group=ANONYMOUS, actions="READ")) == [1, 4]
        assert ids(mgr.filter_policies(pols, policy_types=[PolicyTypes.CUSTOM])) == [4]

    def test_find_group_policies_on_resource(self, mgr, resource_policies):
        resource_policies({"r1": [policy(1), policy(2, action="WRITE"), policy(3, group=ADMIN)]})
        assert [p["id"] for p in mgr.find_group_policies_on_resource("r1")] == [1]
        assert [p["id"] for p in mgr.find_group_policies_on_resource("r1", actions=None)] == [1, 2]

    def test_has_group_access_respects_dates(self, mgr, resource_policies):
        resource_policies({
            "open": [policy(1)],
            "embargoed": [policy(2, start="2999-01-01")],
            "expired": [policy(3, end="2000-01-01")],
            "window": [policy(4, start="2020-01-01", end="2020-12-31")],
            "none": [],
        })
        assert mgr.has_group_access("open") is True
        assert mgr.has_group_access("embargoed") is False
        assert mgr.has_group_access("embargoed", on_date="2999-06-01") is True
        assert mgr.has_group_access("expired") is False
        assert mgr.has_group_access("window", on_date=date(2020, 6, 1)) is True
        assert mgr.has_group_access("none") is False


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

class TestTargets:
    @pytest.fixture
    def item_tree(self, http, api_url):
        """The item with bundles and bitstreams embedded (one request)"""
        http.add(responses.GET, api_url("/core/items/it1"), json=embedded_item("it1", [
            ({"uuid": "bu1", "name": "ORIGINAL"}, [{"uuid": "bs1", "name": "a.pdf"}]),
            ({"uuid": "bu2"}, [{"uuid": "bs2"}]),
        ]))

    def test_item_only(self, mgr, http):
        out = list(mgr.iter_item_resources("it1", "T", include_bundles=False, include_bitstreams=False))
        assert out == [{"type": "item", "uuid": "it1", "name": "T"}]
        assert len(http.calls) == 0

    def test_bundles_and_bitstreams(self, mgr, http, item_tree):
        out = [(r["type"], r["uuid"], r["name"]) for r in mgr.iter_item_resources("it1")]
        assert len(http.calls) == 1                                    # embedded, not 1 + N
        assert http.calls[0].request.params["embed"] == ["bundles", "bundles/bitstreams"]
        assert out == [("item", "it1", ""), ("bundle", "bu1", "ORIGINAL"), ("bitstream", "bs1", "a.pdf"),
                       ("bundle", "bu2", ""), ("bitstream", "bs2", "")]

    def test_bundles_only_and_bitstreams_only(self, mgr, item_tree):
        assert [r["type"] for r in mgr.iter_item_resources("it1", include_bitstreams=False)] == \
            ["item", "bundle", "bundle"]
        assert [r["type"] for r in mgr.iter_item_resources("it1", include_bundles=False)] == \
            ["item", "bitstream", "bitstream"]

    def test_iter_items_in_scope_pages(self, mgr, http, api_url):
        def page(objs, total):
            return {"_embedded": {"searchResult": {"_embedded": {"objects": objs}, "page": {"totalPages": total}}}}
        obj = lambda u: {"_embedded": {"indexableObject": {"uuid": u}}}
        url = api_url("/discover/search/objects")
        http.add(responses.GET, url, json=page([obj("a"), {"_embedded": {}}], 2))
        http.add(responses.GET, url, json=page([obj("b")], 2))
        assert [i["uuid"] for i in mgr.iter_items_in_scope("col")] == ["a", "b"]
        assert http.calls[0].request.params["dsoType"] == "ITEM"
        assert http.calls[0].request.params["scope"] == "col"

    def test_iter_items_in_scope_empty(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/discover/search/objects"), json={"_embedded": {"searchResult": {}}})
        assert list(mgr.iter_items_in_scope("col")) == []


# ---------------------------------------------------------------------------
# Remove / grant / restore
# ---------------------------------------------------------------------------

class TestRemoveGrantRestore:
    def test_remove_dry_run_changes_nothing(self, mgr, http, resource_policies):
        resource_policies({"r1": [policy(1), policy(2, group=ADMIN)]})
        rows = mgr.remove_group_policies(["r1"])
        assert [(r["policy_id"], r["status"], r["resource_type"]) for r in rows] == [(1, "would-delete", "")]
        assert all(c.request.method == "GET" for c in http.calls)
        assert set(rows[0]) == set(POLICY_ROW_FIELDS)

    def test_remove_execute_statuses(self, mgr, http, api_url, resource_policies):
        resource_policies({"r1": [policy(1), policy(2), policy(3)]})
        http.add(responses.DELETE, api_url(f"{POLICIES}/1"), status=204)
        http.add(responses.DELETE, api_url(f"{POLICIES}/2"), status=404)
        http.add(responses.DELETE, api_url(f"{POLICIES}/3"), status=500)
        rows = mgr.remove_group_policies([{"type": "item", "uuid": "r1", "name": "T"}], dry_run=False,
                                         item_uuid="r1")
        assert [r["status"] for r in rows] == ["deleted", "already-gone", "failed:500"]
        assert rows[0]["item_uuid"] == "r1" and rows[0]["group_uuid"] == ANON["uuid"]

    def test_remove_failure_without_status_code_uses_message(self, mgr, resource_policies):
        resource_policies({"r1": [policy(1)]})
        with patch.object(mgr, "delete_policy", side_effect=DSpaceError("boom")):
            rows = mgr.remove_group_policies(["r1"], dry_run=False)
        assert rows[0]["status"] == "failed:boom"

    def test_remove_group_access_from_item(self, mgr, http, api_url, resource_policies):
        http.add(responses.GET, api_url("/core/items/it1"),
                 json=embedded_item("it1", [({"uuid": "bu1"}, [{"uuid": "bs1"}])]))
        resource_policies({"it1": [policy(1)], "bu1": [policy(2)], "bs1": [policy(3, action="WRITE")]})
        rows = mgr.remove_group_access_from_item("it1", include_bundles=True, include_bitstreams=True,
                                                 actions=None)
        assert [(r["resource_type"], r["policy_id"]) for r in rows] == [("item", 1), ("bundle", 2), ("bitstream", 3)]
        assert {r["item_uuid"] for r in rows} == {"it1"}

    def test_grant_skips_identical_existing_policy(self, mgr, http, resource_policies):
        resource_policies({"r1": [policy(1, start="2027-01-01")]})
        assert mgr.grant_group_access("r1", start_date="2027-01-01T00:00:00Z")["id"] == 1
        assert all(c.request.method == "GET" for c in http.calls)

    def test_grant_creates_when_dates_differ_or_skip_disabled(self, mgr, http, api_url, hal_page, resource_policies):
        resource_policies({"r1": [policy(1)]})
        http.add(responses.GET, api_url("/eperson/groups/search/byMetadata"), json=hal_page("groups", [ANON]))
        http.add(responses.POST, api_url(POLICIES), status=201, json={"id": 9})
        assert mgr.grant_group_access("r1", end_date="2030-01-01")["id"] == 9
        assert mgr.grant_group_access("r1", skip_if_exists=False)["id"] == 9

    def test_restore(self, mgr, http, api_url, resource_policies):
        resource_policies({})
        http.add(responses.GET, api_url(f"/eperson/groups/{ANON['uuid']}"), json=ANON)
        http.add(responses.POST, api_url(POLICIES), status=201, json={"id": 42})
        rows = [
            {"status": "would-delete", "resource_uuid": "r0"},                     # ignored
            {"status": "deleted", "resource_uuid": "r1", "group_uuid": "", "group_name": ""},
            {"status": "deleted", "resource_uuid": "r2", "group_uuid": ANON["uuid"], "action": "READ",
             "policy_type": "TYPE_INHERITED", "start_date": "2027-01-01", "end_date": "", "name": "", "description": ""},
        ]
        dry = mgr.restore_policies(rows)
        assert [r["status"] for r in dry] == ["restore-skipped:no-group", "would-restore"]
        assert not any(c.request.method == "POST" for c in http.calls)

        done = mgr.restore_policies(rows, dry_run=False)
        assert [r["status"] for r in done] == ["restore-skipped:no-group", "restored"]
        assert done[1]["policy_id"] == 42
        body = json.loads(last(http).body)
        assert body["startDate"] == "2027-01-01" and body["endDate"] is None
        assert body["policyType"] == "TYPE_INHERITED"
        assert rows[2]["status"] == "deleted"          # input rows are not mutated

    def test_restore_failure(self, mgr):
        with patch.object(mgr, "grant_group_access", side_effect=ServerError("x", status_code=500)):
            out = mgr.restore_policies([{"status": "deleted", "resource_uuid": "r", "group_name": "Anonymous"}],
                                       dry_run=False)
        assert out[0]["status"] == "restore-failed:500"
        with patch.object(mgr, "grant_group_access", side_effect=DSpaceError("nope")):
            out = mgr.restore_policies([{"status": "deleted", "resource_uuid": "r", "group_name": "Anonymous"}],
                                       dry_run=False)
        assert out[0]["status"] == "restore-failed:nope"

    def test_report_roundtrip(self, tmp_path):
        rows = [{f: f"v-{f}" for f in POLICY_ROW_FIELDS}]
        rows[0]["extra"] = "ignored"
        path = tmp_path / "report.csv"
        PermissionManager.write_report(rows, path)
        back = PermissionManager.read_report(path)
        assert list(back[0]) == POLICY_ROW_FIELDS
        assert back[0]["policy_id"] == "v-policy_id"


# ---------------------------------------------------------------------------
# Bulk access control
# ---------------------------------------------------------------------------

OPTIONS = {
    "id": "default",
    "itemAccessConditionOptions": [
        {"name": "openaccess"}, {"name": "administrator"},
        {"name": "embargo", "hasStartDate": True, "maxStartDate": "2030-01-01T00:00:00.000+0000"},
        {"name": "lease", "hasEndDate": True, "maxEndDate": "2030-01-01T00:00:00.000+0000"},
    ],
    "bitstreamAccessConditionOptions": [{"name": "administrator"}, {"name": "embargo", "hasStartDate": True}],
}


class TestBulkAccess:
    @pytest.fixture
    def options(self, http, api_url):
        http.add(responses.GET, api_url("/config/bulkaccessconditionoptions/default"), json=OPTIONS)

    def test_list_and_get_options(self, mgr, http, api_url, hal_page, options):
        http.add(responses.GET, api_url("/config/bulkaccessconditionoptions"),
                 json=hal_page("bulkaccessconditionoptions", [OPTIONS]))
        assert mgr.list_bulk_access_condition_options()[0]["id"] == "default"
        assert mgr.get_bulk_access_condition_options()["id"] == "default"

    @pytest.mark.parametrize("cond,match", [
        ({"name": "nope"}, "not allowed"),
        ({"name": "embargo"}, "requires startDate"),
        ({"name": "lease"}, "requires endDate"),
        ({"name": "embargo", "startDate": "2031-01-01"}, "after maxStartDate"),
        ({"name": "lease", "endDate": "2031-01-01"}, "after maxEndDate"),
        ({"name": "openaccess", "startDate": "2026-05-01", "endDate": "2026-05-01"}, "endDate must be after"),
    ])
    def test_condition_validation(self, cond, match):
        with pytest.raises(ValueError, match=match):
            PermissionManager._validate_conditions([cond], OPTIONS["itemAccessConditionOptions"], "item")

    def test_valid_conditions_pass(self):
        PermissionManager._validate_conditions(
            [{"name": "embargo", "startDate": "2029-01-01"}, {"name": "lease", "endDate": date(2029, 1, 1)},
             {"name": "openaccess", "startDate": "2026-01-01", "endDate": "2026-02-01"}],
            OPTIONS["itemAccessConditionOptions"], "item")
        # options without max dates accept any date
        PermissionManager._validate_conditions([{"name": "embargo", "startDate": "2999-01-01"}],
                                               OPTIONS["bitstreamAccessConditionOptions"], "bitstream")

    def test_build_payload_validated(self, mgr, options):
        payload = mgr.build_bulk_access_payload(
            item_conditions=[{"name": "embargo", "startDate": datetime(2029, 3, 4, 5, 6)}],
            item_mode="add",
            bitstream_conditions=[{"name": "administrator"}],
            bitstream_uuids=["bs1"],
        )
        assert payload == {
            "item": {"mode": "add", "accessConditions": [{"name": "embargo", "startDate": "2029-03-04"}]},
            "bitstream": {"mode": "replace", "accessConditions": [{"name": "administrator"}],
                          "constraints": {"uuid": ["bs1"]}},
        }

    def test_build_payload_unvalidated_item_only_with_end_date(self, mgr, http):
        payload = mgr.build_bulk_access_payload(item_conditions=[{"name": "lease", "endDate": "2030-01-01"}],
                                                validate=False)
        assert payload == {"item": {"mode": "replace",
                                    "accessConditions": [{"name": "lease", "endDate": "2030-01-01"}]}}
        assert len(http.calls) == 0

    def test_build_payload_bitstream_only_validated(self, mgr, options):
        payload = mgr.build_bulk_access_payload(bitstream_conditions=[{"name": "administrator"}])
        assert list(payload) == ["bitstream"] and "constraints" not in payload["bitstream"]

    def test_build_payload_item_only_validated(self, mgr, options):
        payload = mgr.build_bulk_access_payload(item_conditions=[{"name": "openaccess"}])
        assert payload == {"item": {"mode": "replace", "accessConditions": [{"name": "openaccess"}]}}

    def test_build_payload_argument_errors(self, mgr):
        with pytest.raises(ValueError, match="item_conditions and/or"):
            mgr.build_bulk_access_payload()
        with pytest.raises(ValueError, match="mode"):
            mgr.build_bulk_access_payload(item_conditions=[], item_mode="merge", validate=False)

    def test_run_bulk_access_control(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/system/scripts/bulk-access-control/processes"),
                 status=202, json={"processId": 7, "processStatus": "SCHEDULED"})
        payload = {"item": {"mode": "replace", "accessConditions": [{"name": "administrator"}]}}
        assert mgr.run_bulk_access_control(["c1", "c2"], payload)["processId"] == 7
        body = last(http).body
        params = json.loads(body.split(b'name="properties"')[1].split(b"\r\n\r\n", 1)[1].split(b"\r\n--")[0])
        assert params == [{"name": "-u", "value": "c1"}, {"name": "-u", "value": "c2"},
                          {"name": "-f", "value": "bulk-access.json"}]
        assert b'filename="bulk-access.json"' in body and json.dumps(payload).encode() in body

    def test_run_bulk_access_control_needs_targets(self, mgr):
        with pytest.raises(ValueError):
            mgr.run_bulk_access_control([], {})

    def test_wait_for_process(self, mgr, http, api_url):
        url = api_url("/system/processes/7")
        http.add(responses.GET, url, json={"processStatus": "RUNNING"})
        http.add(responses.GET, url, json={"processStatus": "COMPLETED"})
        with patch("dspace_api.permissions.time.sleep") as sleep:
            assert mgr.wait_for_process(7, poll_interval=0.5)["processStatus"] == "COMPLETED"
        sleep.assert_called_once_with(0.5)

    def test_wait_for_process_failed(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/system/processes/7"), json={"processStatus": "FAILED"})
        with pytest.raises(DSpaceError, match="failed"):
            mgr.wait_for_process(7)

    def test_wait_for_process_timeout(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/system/processes/7"), json={"processStatus": "RUNNING"})
        with patch("dspace_api.permissions.time.monotonic", side_effect=[0, 11]), \
             patch("dspace_api.permissions.time.sleep"):
            with pytest.raises(DSpaceError, match="still RUNNING"):
                mgr.wait_for_process(7, timeout=10)
