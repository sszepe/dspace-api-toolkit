"""Archived items: CRUD and metadata upserts"""

import pytest

from dspace_api.exceptions import NotFoundError

from .helpers import values


@pytest.mark.feature("items.crud")
def test_item_crud(items, collection, entity_type, run_id):
    item = items.create_item(collection["uuid"], {"dc.title": f"IT crud {run_id}", "dc.date.issued": "2026"},
                             entity_type)
    try:
        fetched = items.get_item(item["uuid"])
        assert values(fetched, "dc.title") == [f"IT crud {run_id}"]
        items.update_item(item["uuid"], "replace", {"dc.title": f"IT crud {run_id} (renamed)"})
        assert values(items.get_item(item["uuid"]), "dc.title") == [f"IT crud {run_id} (renamed)"]
    finally:
        assert items.delete_item(item["uuid"]) is True
    with pytest.raises(NotFoundError):
        items.get_item(item["uuid"])


@pytest.mark.feature("items.upsert")
def test_upsert_metadata(items, new_item):
    item = new_item()
    items.upsert_metadata(item["uuid"], {"dc.title": "Upserted title", "dc.subject": ["alpha", "beta"]})
    fetched = items.get_item(item["uuid"])
    assert values(fetched, "dc.title") == ["Upserted title"]                 # replaced
    assert values(fetched, "dc.subject") == ["alpha", "beta"]                # added
    items.upsert_metadata(item["uuid"], {"dc.subject": ["gamma"]})           # replace again, no duplicates
    assert values(items.get_item(item["uuid"]), "dc.subject") == ["gamma"]


@pytest.mark.feature("items.upsert_by_position")
def test_upsert_metadata_by_position(items, new_item):
    item = new_item(metadata={"dc.subject": ["a", "b"]})
    items.upsert_metadata_by_position(item["uuid"], {"dc.subject": {"value": "B2", "place": 1}})
    assert values(items.get_item(item["uuid"]), "dc.subject") == ["a", "B2"]
    items.upsert_metadata_by_position(item["uuid"], {"dc.subject": {"value": "c", "place": 2}})
    assert values(items.get_item(item["uuid"]), "dc.subject") == ["a", "B2", "c"]
    items.upsert_metadata_by_position(item["uuid"], {"dc.rights": {"value": "CC BY", "place": 0}})
    assert values(items.get_item(item["uuid"]), "dc.rights") == ["CC BY"]
