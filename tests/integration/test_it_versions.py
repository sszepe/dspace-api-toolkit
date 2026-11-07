"""Item versioning"""

import pytest

from .helpers import unsupported_on, values


def versionable_item(new_item, versions):
    """An archived item; entity items may be blocked from versioning (versioning.block.entity)."""
    item = new_item()
    with unsupported_on(403, 405, 422, what="version creation (versioning disabled or blocked for entities)"):
        version = versions.create_version(item["uuid"], "IT draft")
    return item, version


@pytest.mark.feature("versions.create")
def test_new_version_flow(versions, items, new_item, cleanup):
    item = new_item(title="IT versioned v1")
    with unsupported_on(403, 405, what="version creation (versioning disabled or blocked for entities)"):
        result = versions.new_version(item["uuid"], "IT second version", metadata={"dc.title": "IT versioned v2"},
                                      strict_metadata=False)
    cleanup(lambda: items.delete_item(result["item_uuid"]))
    assert result["item_uuid"] != item["uuid"]

    listed = versions.list_item_versions(item["uuid"])
    assert [int(v["version"]) for v in listed][:2] == [2, 1]
    assert versions.latest_version(item["uuid"])["id"] == result["version"]["id"]
    assert versions.is_latest(result["item_uuid"]) is True
    assert versions.is_latest(item["uuid"]) is False
    assert values(items.get_item(result["item_uuid"]), "dc.title") == ["IT versioned v2"]

    updated = versions.set_summary(result["version"]["id"], "edited summary")
    assert updated.get("summary") == "edited summary"


@pytest.mark.feature("versions.draft")
def test_draft_detection_and_discard(versions, new_item):
    item, version = versionable_item(new_item, versions)
    history = versions.history_of_item(item["uuid"])
    draft = versions.get_draft_version(history["id"])
    assert draft and draft.get("type") == "workspaceitem"
    assert versions.discard_draft(item["uuid"]) is True
    assert versions.get_draft_version(history["id"]) is None
    assert versions.discard_draft(item["uuid"]) is False
