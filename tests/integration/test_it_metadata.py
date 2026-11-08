"""Metadata: generic MetadataPatch on archived objects and the metadata registry"""

import pytest

from dspace_api.exceptions import DSpaceError
from dspace_api.metadata import MetadataPatch

from .helpers import values


@pytest.mark.feature("metadata.patch")
def test_metadata_patch_operations(metadata, items, new_item):
    item = new_item(metadata={"dc.subject": ["a", "b", "c"]})

    def subjects():
        return [v["value"] for v in metadata.get_metadata("item", item["uuid"]).get("dc.subject") or []]

    # one request per step: DSpace does not renumber places between the
    # operations of one PATCH, so index-based steps in one request interfere
    steps = [
        (MetadataPatch().insert("dc.subject", 0, "first"), ["first", "a", "b", "c"]),
        (MetadataPatch().remove("dc.subject", 2), ["first", "a", "c"]),
        (MetadataPatch().move("dc.subject", 2, 0), ["c", "first", "a"]),
        (MetadataPatch().replace_value("dc.subject", 1, "FIRST"), ["c", "FIRST", "a"]),
        (MetadataPatch().append("dc.subject", "z"), ["c", "FIRST", "a", "z"]),
        (MetadataPatch().set("dc.subject", ["x", "y"]), ["x", "y"]),        # all values, not only the first
        (MetadataPatch().replace("dc.subject", ["p", "q", "r"]), ["p", "q", "r"]),
    ]
    for patch, expected in steps:
        metadata.patch_metadata("item", item["uuid"], patch, check_fields=True)
        assert subjects() == expected, patch.operations

    metadata.patch_metadata("item", item["uuid"],
                            MetadataPatch().set("dc.description", {"value": "Beschreibung", "language": "de"}))
    metadata.patch_metadata("item", item["uuid"], MetadataPatch().set_property("dc.description", 0, "language", "en"))
    assert metadata.get_metadata("item", item["uuid"])["dc.description"][0]["language"] == "en"

    metadata.patch_metadata("item", item["uuid"], MetadataPatch().remove("dc.description"))
    assert values(items.get_item(item["uuid"]), "dc.description") == []


@pytest.mark.feature("metadata.patch")
def test_raw_multi_value_add_keeps_every_value(metadata, items, new_item):
    """A plain 'add' with an array (as upsert_metadata sends) stores every value, not only the first."""
    item = new_item()
    items.update_item(item["uuid"], "add", {"dc.subject": ["one", "two", "three"]})
    assert values(items.get_item(item["uuid"]), "dc.subject") == ["one", "two", "three"]
    items.update_item(item["uuid"], "replace", {"dc.subject": ["four", "five"]})
    assert values(items.get_item(item["uuid"]), "dc.subject") == ["four", "five"]


@pytest.mark.feature("metadata.patch")
def test_metadata_patch_on_collection(metadata, new_collection):
    coll = new_collection()
    metadata.patch_metadata("collection", coll["uuid"], MetadataPatch().set("dc.description.abstract", "Abstract"))
    assert metadata.get_metadata("collection", coll["uuid"])["dc.description.abstract"][0]["value"] == "Abstract"


@pytest.mark.feature("metadata.registry")
def test_registry_roundtrip(metadata, run_id, cleanup):
    assert metadata.find_schema("dc")["prefix"] == "dc"
    assert metadata.field_exists("dc.title")
    assert metadata.find_field("dc.contributor.author")["element"] == "contributor"

    prefix = "it" + run_id.replace("-", "")[-10:]
    schema = metadata.create_schema(prefix, f"http://example.org/toolkit-it/{prefix}")
    cleanup(lambda: metadata.delete_schema(schema["id"]))
    assert metadata.unknown_fields([f"{prefix}.note", "dc.title"]) == [f"{prefix}.note"]

    assert metadata.ensure_fields([f"{prefix}.note", f"{prefix}.project.id"], scope_note="IT") == [
        f"{prefix}.note", f"{prefix}.project.id"]
    field = metadata.find_field(f"{prefix}.project.id", refresh=True)
    assert field and field["qualifier"] == "id"
    assert metadata.update_field(field["id"], "changed")["scopeNote"] == "changed"
    assert metadata.update_schema(schema["id"], "http://example.org/changed")["namespace"] == "http://example.org/changed"
    assert len(metadata.get_fields_by_schema(prefix)) == 2

    for name in (f"{prefix}.note", f"{prefix}.project.id"):
        metadata.delete_field(metadata.find_field(name, refresh=True)["id"])
    assert metadata.find_field(f"{prefix}.note", refresh=True) is None


@pytest.mark.feature("metadata.registry")
def test_registry_rejects_invalid_schema(metadata):
    with pytest.raises(DSpaceError):
        metadata.create_schema("bad.prefix", "http://example.org/bad")
