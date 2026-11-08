"""Resource policies and bulk access control"""

from datetime import date, timedelta

import pytest

from dspace_api.permissions import ANONYMOUS, Actions, PolicyTypes

from .helpers import unsupported, unsupported_on


@pytest.mark.feature("policies.read")
def test_read_item_policies(permissions, new_item):
    item = new_item()
    policies = permissions.get_resource_policies(item["uuid"])
    assert policies, "a new item should carry policies inherited from its collection"
    assert all("group" in (p.get("_embedded") or {}) for p in policies)


@pytest.mark.feature("policies.remove_restore")
def test_remove_and_restore_anonymous_read(permissions, bundles, new_item):
    item = new_item()
    bundle = bundles.get_or_create_bundle(item["uuid"])
    bundles.upload_bitstream(bundle["uuid"], b"public?", name="file.txt")
    if not permissions.has_group_access(item["uuid"], ANONYMOUS):
        pytest.skip("collection gives no Anonymous READ to new items - nothing to remove")

    rows = permissions.remove_group_access_from_item(item["uuid"], include_bitstreams=True, dry_run=False)
    assert rows and {r["status"] for r in rows} == {"deleted"}
    assert not permissions.has_group_access(item["uuid"], ANONYMOUS)

    restored = permissions.restore_policies(rows, dry_run=False)
    assert {r["status"] for r in restored} == {"restored"}
    assert permissions.has_group_access(item["uuid"], ANONYMOUS)


@pytest.mark.feature("policies.create_update")
def test_create_update_delete_policy(permissions, new_item):
    item = new_item()
    start = date.today() + timedelta(days=30)
    policy = permissions.create_policy(item["uuid"], Actions.READ, group="Administrator",
                                       policy_type=PolicyTypes.CUSTOM, name="IT embargo", start_date=start)
    try:
        assert policy["startDate"].startswith(start.isoformat())
        updated = permissions.update_policy(policy["id"], start_date=None, end_date=start + timedelta(days=365),
                                            description="updated by IT")
        assert updated["startDate"] is None and updated["description"] == "updated by IT"
    finally:
        assert permissions.delete_policy(policy["id"]) is True


@pytest.mark.feature("bulk_access.options")
def test_bulk_access_condition_options(permissions):
    with unsupported_on(what="bulkaccessconditionoptions"):
        options = permissions.get_bulk_access_condition_options()
    assert "itemAccessConditionOptions" in options


@pytest.mark.feature("bulk_access.run")
def test_bulk_access_control_run(permissions, new_item):
    item = new_item()
    with unsupported_on(what="bulkaccessconditionoptions"):
        options = permissions.get_bulk_access_condition_options()
    if "administrator" not in {o["name"] for o in options.get("itemAccessConditionOptions", [])}:
        unsupported("no 'administrator' item access condition configured")
    payload = permissions.build_bulk_access_payload(item_conditions=[{"name": "administrator"}])
    with unsupported_on(404, what="bulk-access-control script"):
        process = permissions.run_bulk_access_control([item["uuid"]], payload)
    permissions.wait_for_process(process["processId"], timeout=600, poll_interval=5)
    assert not permissions.has_group_access(item["uuid"], ANONYMOUS)
