"""Review process: workflow items, tasks, decisions, configuration and supervision orders"""

import pytest

from .helpers import eventually, unsupported, unsupported_on, values


def required_fields(errors):
    """{section: [field]} from error.validation.required paths like /sections/<s>/<field>."""
    result = {}
    for error in errors:
        if error.get("message") != "error.validation.required":
            continue
        for path in error.get("paths") or []:
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "sections" and parts[2].count(".") in (1, 2):
                result.setdefault(parts[1], []).append(parts[2])
    return result


@pytest.fixture
def review_collection(collections, new_collection, me):
    """A collection whose review step is done by the test admin."""
    coll = new_collection()
    with unsupported_on(404, 405, 422, what="collection workflow groups"):
        collections.add_role_members(coll["uuid"], "workflowGroups/reviewer", epersons=[me["uuid"]])
    return coll


@pytest.fixture
def deposit(workspace, items, entity_type, run_id, cleanup):
    """Factory: submit an item into review in a collection, returns (item uuid, workspace item id)."""
    counter = iter(range(1, 100))

    def factory(collection, title=None):
        title = title or f"IT review {run_id}-{next(counter)}"
        result = workspace.submit(collection["uuid"], {"dc.title": title}, entity_type=entity_type, deposit=False,
                                  strict_metadata=False)
        ws_id, item_uuid = result["workspace_item"]["id"], result["item_uuid"]
        cleanup(lambda: items.delete_item(item_uuid))
        for section, fields in required_fields(result["errors"]).items():
            workspace.patch_section_metadata(ws_id, section, {f: "2026" if "date" in f else "Toolkit IT"
                                                              for f in fields})
        workspace.deposit(ws_id)
        return item_uuid, ws_id
    return factory


@pytest.mark.feature("workflow.config")
def test_workflow_configuration(workflow, review_collection):
    definitions = workflow.get_workflow_definitions()
    assert definitions
    default = next((d for d in definitions if d.get("isDefault")), definitions[0])
    assert workflow.get_workflow_definition(default["name"])["name"] == default["name"]
    assert workflow.definition_for_collection(review_collection)["name"]
    steps = workflow.definition_steps(default["name"])
    assert steps
    step = workflow.get_workflow_step(steps[0]["id"])
    actions = ((step.get("_embedded") or {}).get("workflowactions") or [])
    if actions:
        assert workflow.get_workflow_action(actions[0]["id"]).get("options") is not None


@pytest.mark.feature("workflow.items")
def test_workflow_item(workflow, workspace, review_collection, deposit, me):
    item_uuid, _ = deposit(review_collection)
    wfi = eventually(lambda: workflow.find_by_item(item_uuid), timeout=30, message="no workflow item")
    assert workflow.get_workflow_item(wfi)["id"] == wfi["id"]
    assert workflow.get_workflow_item_link(wfi, "item")["uuid"] == item_uuid
    assert workflow.get_workflow_item_link(wfi, "collection")["uuid"] == review_collection["uuid"]
    assert workflow.get_workflow_item_link(wfi, "step") is not None
    assert wfi["id"] in [w["id"] for w in workflow.find_by_submitter(me)]

    assert workflow.return_to_workspace(wfi)
    assert workflow.find_by_item(item_uuid) is None
    assert workspace.find_by_item(item_uuid) is not None


@pytest.mark.feature("workflow.review")
def test_reject_then_approve(workflow, workspace, items, review_collection, deposit, me):
    item_uuid, ws_id = deposit(review_collection)
    pending = [t for t in workflow.pending_tasks() if t["item_uuid"] == item_uuid]
    if not pending:
        unsupported("no review task for the depositing admin (workflow not configured on this server?)")
    assert pending[0]["kind"] == "pool"
    pool = workflow.pool_tasks_for_item(item_uuid)
    assert pool and all(((t.get("_embedded") or {}).get("workflowitem") or {}).get("id") == pending[0]["workflow_item_id"]
                        for t in pool)

    claimed = workflow.claim_for_item(item_uuid)
    options = workflow.task_options(claimed)
    assert "approve" in options and "reject" in options, options
    assert workflow.find_pool_task(item_uuid) is None
    assert workflow.unclaim(claimed)
    assert workflow.find_pool_task(item_uuid) is not None

    result = workflow.review(item_uuid, "reject", reason="Toolkit IT: please add an abstract")
    assert result["workflow_item"] is None
    back = workspace.find_by_item(item_uuid)
    assert back is not None

    workspace.deposit(back["id"])
    assert workflow.approve_all_steps(item_uuid) >= 1
    assert workflow.find_by_item(item_uuid) is None
    archived = items.get_item(item_uuid)
    assert archived.get("inArchive") is True
    assert values(archived, "dc.title")


@pytest.mark.feature("workflow.supervision")
def test_supervision_order(workflow, workspace, collection, new_group, entity_type, run_id, cleanup):
    ws = workspace.start_submission(collection["uuid"], entity_type)
    cleanup(lambda: workspace.delete(ws["id"]))
    item_uuid = workspace.get_item_of(ws["id"])["uuid"]
    supervisors = new_group()
    with unsupported_on(404, 405, what="supervision orders"):
        order = workflow.create_supervision_order(item_uuid, supervisors, "OBSERVER")
    assert order["id"] in [o["id"] for o in workflow.supervision_orders_for_item(item_uuid)]
    assert workflow.get_supervision_order(order["id"])["id"] == order["id"]
    assert order["id"] in [o["id"] for o in workflow.iter_supervision_orders()]
    assert workflow.delete_supervision_order(order["id"])
    assert workflow.supervision_orders_for_item(item_uuid) == []
