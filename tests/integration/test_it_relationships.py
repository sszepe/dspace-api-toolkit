"""
Relationships between entity items (plain DSpace with the entity model loaded;
DSpace-CRIS normally has no relationship types -> "unsupported").
"""

import pytest

from dspace_api.collections import build_collection_metadata

from .helpers import unsupported, values

AUTHOR = "isAuthorOfPublication"


@pytest.fixture(scope="module")
def entity_model(relationships):
    try:
        relationships.resolve_type(AUTHOR, "Publication", "Person")
    except ValueError as exc:
        unsupported(f"no Publication-Person relationship type ({exc})")
    return True


@pytest.fixture
def person_collection(collections, sandbox, run_id, cleanup):
    title = f"IT persons {run_id}"
    coll = collections.create_collection(sandbox["uuid"], title,
                                         metadata=build_collection_metadata(title, entity_type="Person"))
    cleanup(lambda: collections.delete_collection(coll["uuid"]))
    return coll


@pytest.fixture
def people(items, person_collection, run_id, cleanup):
    def factory(*names):
        created = []
        for name in names:
            person = items.create_item(person_collection["uuid"], {"dc.title": f"{name} {run_id}"}, "Person")
            cleanup(lambda uuid=person["uuid"]: items.delete_item(uuid))
            created.append(person)
        return created
    return factory


@pytest.mark.feature("relationships.types")
def test_relationship_types(relationships, entity_types, entity_model):
    types = relationships.get_relationship_types()
    assert types
    rtype, side = relationships.resolve_type(AUTHOR, "Publication", "Person")
    assert side == "left" and relationships.type_entity_labels(rtype) == ("Publication", "Person")
    assert rtype["id"] in [t["id"] for t in relationships.get_relationship_types_for_entity("Person")]
    assert rtype["id"] in [t["id"] for t in entity_types.get_relationship_types("Publication")]


@pytest.mark.feature("relationships.crud")
def test_relate_and_unrelate(relationships, items, new_item, people, entity_model, entity_type):
    if entity_type != "Publication":
        unsupported("sandbox collection is not a Publication collection")
    publication = new_item()
    jane, = people("Doe, Jane")
    rel = relationships.relate(publication, AUTHOR, jane, name_variant="Doe, J.")
    assert relationships.ensure_related(publication, AUTHOR, jane)[1] is False

    related = relationships.related_items(publication, AUTHOR)
    assert [(r["item_uuid"], r["name_variant"]) for r in related] == [(jane["uuid"], "Doe, J.")]
    back = relationships.related_items(jane, "isPublicationOfAuthor")
    assert [r["item_uuid"] for r in back] == [publication["uuid"]]
    assert rel["id"] in [r["id"] for r in relationships.iter_item_relationships(publication)]
    assert rel["id"] in [r["id"] for r in relationships.iter_relationships(embed=None)]

    # the author shows up as virtual metadata on the publication
    authors = values(items.get_item(publication["uuid"]), "dc.contributor.author")
    assert "Doe, J." in authors, authors

    assert relationships.unrelate(publication, AUTHOR, jane, copy_virtual_metadata="left") == 1
    assert relationships.related_items(publication, AUTHOR) == []
    # copyVirtualMetadata=left keeps the author as real metadata on the publication
    assert "Doe, J." in values(items.get_item(publication["uuid"]), "dc.contributor.author")


@pytest.mark.feature("relationships.order")
def test_order_variant_and_replace(relationships, new_item, people, entity_model, entity_type):
    if entity_type != "Publication":
        unsupported("sandbox collection is not a Publication collection")
    publication = new_item()
    first, second, third = people("First, A", "Second, B", "Third, C")
    for person in (first, second):
        relationships.relate(publication, AUTHOR, person)
    assert [r["item_uuid"] for r in relationships.related_items(publication, AUTHOR)] == [
        first["uuid"], second["uuid"]]

    reordered = relationships.reorder(publication, AUTHOR, [second, first])
    assert [r["item_uuid"] for r in reordered] == [second["uuid"], first["uuid"]]

    rel_id = reordered[0]["relationship_id"]
    relationships.set_name_variant(rel_id, "left", "Second, B.")
    assert relationships.related_items(publication, AUTHOR)[0]["name_variant"] == "Second, B."

    relationships.replace_item(rel_id, "right", third)
    uuids = [r["item_uuid"] for r in relationships.related_items(publication, AUTHOR)]
    assert third["uuid"] in uuids and second["uuid"] not in uuids
    assert relationships.delete_item_relationships(publication) == 2
