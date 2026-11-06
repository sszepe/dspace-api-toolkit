"""
Tests for relationships.py (relationship types, relationships, relate/unrelate/reorder)
and the entity type additions.
"""

import json
from urllib.parse import parse_qsl, urlparse

import pytest
import responses

from dspace_api.entity_types import EntityTypeManager
from dspace_api.exceptions import NotFoundError, ServerError
from dspace_api.relationships import RelationshipManager, entity_type_of

API = "http://localhost:8080/server/api"
PUB = "10000000-0000-0000-0000-000000000001"
P1 = "20000000-0000-0000-0000-000000000001"
P2 = "20000000-0000-0000-0000-000000000002"
ORG = "30000000-0000-0000-0000-000000000001"

PUBLICATION = {"uuid": PUB, "entityType": "Publication"}
PERSON1 = {"uuid": P1, "entityType": "Person"}
PERSON2 = {"uuid": P2, "metadata": {"dspace.entity.type": [{"value": "Person"}]}}
ORGUNIT = {"uuid": ORG, "entityType": "OrgUnit"}


def rtype(type_id, left, right, leftward, rightward, embed=True):
    data = {"id": type_id, "leftwardType": leftward, "rightwardType": rightward, "type": "relationshiptype",
            "_links": {"leftType": {"href": f"{API}/core/relationshiptypes/{type_id}/leftType"},
                       "rightType": {"href": f"{API}/core/relationshiptypes/{type_id}/rightType"}}}
    if embed:
        data["_embedded"] = {"leftType": {"label": left}, "rightType": {"label": right}}
    return data


AUTHOR = rtype(1, "Publication", "Person", "isAuthorOfPublication", "isPublicationOfAuthor")
AUTHOR_ORG = rtype(17, "Publication", "OrgUnit", "isAuthorOfPublication", "isPublicationOfAuthor")
PERSON_ORG = rtype(5, "Person", "OrgUnit", "isOrgUnitOfPerson", "isPersonOfOrgUnit", embed=False)


def relationship(rel_id, left, right, left_place=0, right_place=0, type_=AUTHOR, **extra):
    # as DSpace sends it: no leftId/rightId, the items only as links
    return {"id": rel_id, "leftPlace": left_place, "rightPlace": right_place,
            "type": "relationship", "_embedded": {"relationshipType": type_},
            "_links": {"leftItem": {"href": f"{API}/core/items/{left}"},
                       "rightItem": {"href": f"{API}/core/items/{right}"},
                       "self": {"href": f"{API}/core/relationships/{rel_id}"}}, **extra}


@pytest.fixture
def mgr(http_auth):
    return RelationshipManager(http_auth)


@pytest.fixture
def model(http, api_url, hal_page):
    http.add(responses.GET, api_url("/core/relationshiptypes"),
             json=hal_page("relationshiptypes", [AUTHOR, AUTHOR_ORG, PERSON_ORG]))
    http.add(responses.GET, api_url("/core/relationshiptypes/5/leftType"), json={"label": "Person"})
    http.add(responses.GET, api_url("/core/relationshiptypes/5/rightType"), json={"label": "OrgUnit"})
    for uuid, item in ((PUB, PUBLICATION), (P1, PERSON1), (P2, PERSON2), (ORG, ORGUNIT)):
        http.add(responses.GET, api_url(f"/core/items/{uuid}"), json=item)
    return http


def last(http, method=None):
    return [c.request for c in http.calls if method is None or c.request.method == method][-1]


def params(request):
    return parse_qsl(urlparse(request.url).query)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class TestTypes:
    def test_entity_type_of(self):
        assert entity_type_of(PUBLICATION) == "Publication"
        assert entity_type_of(PERSON2) == "Person"
        assert entity_type_of({"metadata": {}}) is None

    def test_list_cached_and_embed(self, mgr, model):
        assert [t["id"] for t in mgr.get_relationship_types()] == [1, 17, 5]
        assert ("embed", "leftType") in params(model.calls[-1].request)
        calls = len(model.calls)
        mgr.get_relationship_types()
        assert len(model.calls) == calls
        mgr.get_relationship_types(refresh=True)
        assert len(model.calls) == calls + 1

    def test_single_and_by_entity(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/relationshiptypes/1"), json=AUTHOR)
        http.add(responses.GET, api_url("/core/relationshiptypes/search/byEntityType"),
                 json=hal_page("relationshiptypes", [AUTHOR]))
        assert mgr.get_relationship_type(1)["leftwardType"] == "isAuthorOfPublication"
        assert mgr.get_relationship_types_for_entity("Publication") == [AUTHOR]
        assert ("type", "Publication") in params(last(http))

    def test_type_entity_labels_from_links_cached(self, mgr, model):
        assert mgr.type_entity_labels(AUTHOR) == ("Publication", "Person")
        assert mgr.type_entity_labels(PERSON_ORG) == ("Person", "OrgUnit")
        calls = len(model.calls)
        mgr.type_entity_labels(PERSON_ORG)
        assert len(model.calls) == calls
        assert mgr.type_entity_labels({"id": 99}) == (None, None)

    def test_resolve_type(self, mgr, model):
        assert mgr.resolve_type("isAuthorOfPublication", "Publication", "Person") == (AUTHOR, "left")
        assert mgr.resolve_type("isPublicationOfAuthor", "OrgUnit", "Publication") == (AUTHOR_ORG, "right")
        assert mgr.resolve_type("isPersonOfOrgUnit")[1] == "right"
        with pytest.raises(ValueError, match="ambiguous"):
            mgr.resolve_type("isAuthorOfPublication")
        with pytest.raises(ValueError, match="No relationship type .* for Person -> Person"):
            mgr.resolve_type("isAuthorOfPublication", "Person", "Person")
        with pytest.raises(ValueError, match="initialize-entities"):
            mgr.resolve_type("isNothing")

    def test_resolve_with_type_id(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/relationshiptypes/1"), json=AUTHOR)
        assert mgr._resolve(PUB, "isAuthorOfPublication", P1, type_id=1)[1] == "left"
        assert mgr._resolve(P1, "isPublicationOfAuthor", PUB, type_id=1)[1] == "right"
        with pytest.raises(ValueError, match="has no label"):
            mgr._resolve(PUB, "isOrgUnitOfPerson", P1, type_id=1)

    def test_item_refs(self, mgr, model):
        assert mgr._entity_type(PERSON1) == "Person"
        assert mgr._entity_type(P2) == "Person"                 # fetched
        with pytest.raises(ValueError):
            mgr._item_uuid("not a uuid")


class TestEntityTypes:
    def test_entity_type_and_relationship_types(self, http_auth, http, api_url, hal_page):
        mgr = EntityTypeManager(http_auth)
        http.add(responses.GET, api_url("/core/entitytypes/1"), json={"id": 1, "label": "Publication"})
        http.add(responses.GET, api_url("/core/entitytypes/label/Publication"), json={"id": 1})
        http.add(responses.GET, api_url("/core/entitytypes/1/relationshiptypes"),
                 json=hal_page("relationshiptypes", [AUTHOR]))
        assert mgr.get_entity_type(1)["label"] == "Publication"
        for ref in (1, "1", {"id": 1}, "Publication"):
            assert mgr.get_relationship_types(ref) == [AUTHOR]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class TestRead:
    def test_get_and_items(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/relationships/7"), json=relationship(7, PUB, P1))
        http.add(responses.GET, api_url(f"/core/items/{PUB}"), json=PUBLICATION)
        assert mgr.get_relationship(7, embed="relationshipType")["id"] == 7
        assert mgr.get_relationship_item(7, "left")["uuid"] == PUB
        assert mgr.get_relationship_item(relationship(7, PUB, P1), "left")["uuid"] == PUB
        with pytest.raises(ValueError):
            mgr.get_relationship_item(7, "top")
        with pytest.raises(ValueError, match="no rightItem link"):
            mgr.get_relationship_item({"id": 9}, "right")

    def test_iter_relationships(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/relationships"),
                 json=hal_page("relationships", [relationship(1, PUB, P1), relationship(2, PUB, P2)]))
        assert [r["id"] for r in mgr.iter_relationships(max_results=1)] == [1]
        assert ("embed", "relationshipType") in params(last(http))
        assert len(list(mgr.iter_relationships(embed=None, page_size=10))) == 2
        assert "embed" not in dict(params(last(http))) and dict(params(last(http)))["size"] == "10"

    def test_ends(self, mgr, http, api_url):
        assert mgr.ends({"leftId": PUB, "rightId": P1}) == (PUB, P1)
        assert mgr.ends({"_embedded": {"leftItem": {"uuid": PUB}, "rightItem": {"uuid": P1}}}) == (PUB, P1)
        assert mgr.ends(relationship(8, PUB, P1)) == (PUB, P1)        # uuid from the item links
        # a link without the uuid at the end -> the linked item is fetched
        http.add(responses.GET, api_url("/core/items/left-link"), json=PUBLICATION)
        http.add(responses.GET, api_url("/core/items/right-link"), json=PERSON1)
        odd = {"id": 8, "_links": {"leftItem": {"href": f"{API}/core/items/left-link"},
                                   "rightItem": {"href": f"{API}/core/items/right-link?x=1"}}}
        assert mgr.ends(odd) == (PUB, P1)
        with pytest.raises(ValueError, match="no leftItem link"):    # no ids, no embeds, no links
            mgr.ends({"id": 9})

    def test_item_relationships_and_by_label(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"/core/items/{PUB}/relationships"),
                 json=hal_page("relationships", [relationship(1, PUB, P1)]))
        http.add(responses.GET, api_url("/core/relationships/search/byLabel"),
                 json=hal_page("relationships", [relationship(1, PUB, P1)]))
        assert [r["id"] for r in mgr.iter_item_relationships(PUBLICATION)] == [1]
        assert ("embed", "relationshipType") in params(last(http))
        mgr.search_by_label("isAuthorOfPublication", dso=PUB, related_entity_type="Person", max_results=5)
        assert params(last(http))[:3] == [("label", "isAuthorOfPublication"), ("dso", PUB),
                                          ("relatedEntityType", "Person")]
        mgr.search_by_label("isAuthorOfPublication")
        assert dict(params(last(http))).keys() == {"label", "page", "size"}

    def test_find_relationships(self, mgr, model, api_url, hal_page):
        model.add(responses.GET, api_url("/core/relationships/search/byItemsAndType"),
                  json=hal_page("relationships", [relationship(1, PUB, P1)]))
        found = mgr.find_relationships(PUBLICATION, "isAuthorOfPublication", [PERSON1, P2])
        assert [r["id"] for r in found] == [1]
        assert params(last(model)) == [("typeId", "1"), ("relationshipLabel", "isAuthorOfPublication"),
                                       ("focusItem", PUB), ("relatedItem", P1), ("relatedItem", P2),
                                       ("page", "0"), ("size", "100")]
        assert mgr.find_relationships(PUBLICATION, "isAuthorOfPublication", []) == []
        mgr.find_relationships(PUB, "isAuthorOfPublication", P1)
        assert ("relatedItem", P1) in params(last(model))

    def test_related_items_from_both_sides(self, mgr, http, api_url, hal_page):
        rels = [relationship(2, PUB, P2, left_place=1, leftwardValue="Roe, R."),
                relationship(1, PUB, P1, left_place=0),
                relationship(9, P1, PUB, left_place=0, type_={"id": 3, "leftwardType": "other", "rightwardType": "otherBack"}),
                {"id": 4, "leftId": PUB, "rightId": ORG}]                       # no embedded type: accepted
        http.add(responses.GET, api_url("/core/relationships/search/byLabel"), json=hal_page("relationships", rels))
        assert mgr.related_items(PUB, "isAuthorOfPublication") == [
            {"relationship_id": 1, "item_uuid": P1, "place": 0, "name_variant": None, "type_id": 1},
            {"relationship_id": 2, "item_uuid": P2, "place": 1, "name_variant": "Roe, R.", "type_id": 1},
            {"relationship_id": 4, "item_uuid": ORG, "place": None, "name_variant": None, "type_id": None},
        ]
        http.replace(responses.GET, api_url("/core/relationships/search/byLabel"),
                     json=hal_page("relationships", [relationship(1, PUB, P1, right_place=3, rightwardValue="J.")]))
        assert mgr.related_items(P1, "isPublicationOfAuthor") == [
            {"relationship_id": 1, "item_uuid": PUB, "place": 3, "name_variant": "J.", "type_id": 1}]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

class TestWrite:
    def test_create(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/core/relationships"), json={"id": 11}, status=201)
        assert mgr.create_relationship(1, PUB, PERSON1, leftward_value="Doe, J.", rightward_value="Pub")["id"] == 11
        request = last(http)
        assert request.headers["Content-Type"] == "text/uri-list"
        assert request.body == f"{API}/core/items/{PUB}\n{API}/core/items/{P1}"
        assert params(request) == [("relationshipType", "1"), ("leftwardValue", "Doe, J."),
                                   ("rightwardValue", "Pub")]
        mgr.create_relationship(1, PUB, P1)
        assert params(last(http)) == [("relationshipType", "1")]

    def test_relate_orders_items(self, mgr, model, api_url):
        model.add(responses.POST, api_url("/core/relationships"), json={"id": 12}, status=201)
        mgr.relate(PUBLICATION, "isAuthorOfPublication", PERSON1, name_variant="Doe, J.")
        request = last(model, "POST")
        assert request.body == f"{API}/core/items/{PUB}\n{API}/core/items/{P1}"
        assert ("leftwardValue", "Doe, J.") in params(request)
        mgr.relate(PERSON1, "isPublicationOfAuthor", PUB, name_variant="Paper")   # focus on the right side
        request = last(model, "POST")
        assert request.body == f"{API}/core/items/{PUB}\n{API}/core/items/{P1}"
        assert ("rightwardValue", "Paper") in params(request) and ("relationshipType", "1") in params(request)

    def test_ensure_related(self, mgr, model, api_url, hal_page):
        url = api_url("/core/relationships/search/byItemsAndType")
        model.add(responses.GET, url, json=hal_page("relationships", [relationship(1, PUB, P1)]))
        model.add(responses.POST, api_url("/core/relationships"), json={"id": 13}, status=201)
        assert mgr.ensure_related(PUB, "isAuthorOfPublication", P1) == (relationship(1, PUB, P1), False)
        model.replace(responses.GET, url, json=hal_page("relationships", []))
        assert mgr.ensure_related(PUB, "isAuthorOfPublication", P1) == ({"id": 13}, True)

    def test_update_merges_current_values(self, mgr, http, api_url):
        url = api_url("/core/relationships/7")
        http.add(responses.GET, url, json=relationship(7, PUB, P1, left_place=2, right_place=0,
                                                       leftwardValue="Old", rightwardValue="Keep"))
        http.add(responses.PUT, url, json={"id": 7})
        mgr.update_relationship(7, left_place=0, leftward_value=None)
        assert json.loads(last(http, "PUT").body) == {"leftPlace": 0, "rightPlace": 0, "leftwardValue": None,
                                                     "rightwardValue": "Keep"}
        mgr.set_name_variant(7, "right", "New")
        assert json.loads(last(http, "PUT").body)["rightwardValue"] == "New"
        with pytest.raises(ValueError):
            mgr.set_name_variant(7, "middle", "x")

    def test_replace_item(self, mgr, http, api_url):
        http.add(responses.PUT, api_url("/core/relationships/7/rightItem"), json={"id": 7, "rightId": P2})
        assert mgr.replace_item(7, "right", P2)["rightId"] == P2
        assert last(http).body == f"{API}/core/items/{P2}"
        http.replace(responses.PUT, api_url("/core/relationships/7/rightItem"), status=204)
        http.add(responses.GET, api_url("/core/relationships/7"), json={"id": 7})
        assert mgr.replace_item(7, "right", PERSON2) == {"id": 7}
        http.add(responses.PUT, api_url("/core/relationships/7/leftItem"), json={"id": 7, "leftId": PUB})
        assert mgr.replace_item(7, "left", PUBLICATION)["leftId"] == PUB
        assert last(http).body == f"{API}/core/items/{PUB}"
        with pytest.raises(ValueError):
            mgr.replace_item(7, "top", P2)

    def test_delete(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url("/core/relationships/7"), status=204)
        assert mgr.delete_relationship(7) is True
        assert params(last(http)) == []
        mgr.delete_relationship(7, copy_virtual_metadata="left")
        assert params(last(http)) == [("copyVirtualMetadata", "left")]
        with pytest.raises(ValueError):
            mgr.delete_relationship(7, copy_virtual_metadata="both")

    def test_unrelate(self, mgr, model, api_url, hal_page):
        model.add(responses.GET, api_url("/core/relationships/search/byItemsAndType"),
                  json=hal_page("relationships", [relationship(1, PUB, P1), relationship(2, PUB, P1)]))
        model.add(responses.DELETE, api_url("/core/relationships/1"), status=204)
        model.add(responses.DELETE, api_url("/core/relationships/2"), status=204)
        assert mgr.unrelate(PUB, "isAuthorOfPublication", P1, copy_virtual_metadata="all") == 2
        assert params(last(model, "DELETE")) == [("copyVirtualMetadata", "all")]

    def test_delete_item_relationships(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"/core/items/{PUB}/relationships"),
                 json=hal_page("relationships", [relationship(1, PUB, P1), relationship(2, PUB, P2)]))
        http.add(responses.GET, api_url("/core/relationships/search/byLabel"),
                 json=hal_page("relationships", [relationship(1, PUB, P1)]))
        http.add(responses.DELETE, api_url("/core/relationships/1"), status=204)
        http.add(responses.DELETE, api_url("/core/relationships/2"), status=500)
        assert mgr.delete_item_relationships(PUB) == 1                       # 2 failed, logged
        assert mgr.delete_item_relationships(PUB, label="isAuthorOfPublication") == 1


class TestReorder:
    def test_reorder(self, mgr, http, api_url, hal_page):
        by_label = api_url("/core/relationships/search/byLabel")
        before = [relationship(1, PUB, P1, left_place=1), relationship(2, PUB, P2, left_place=2)]
        after = [relationship(1, PUB, P1, left_place=2), relationship(2, PUB, P2, left_place=1)]
        http.add(responses.GET, by_label, json=hal_page("relationships", before))
        http.add(responses.GET, by_label, json=hal_page("relationships", after))     # after the first PUT
        http.add(responses.GET, api_url("/core/relationships/2"), json=after[1] | {"leftPlace": 2})
        http.add(responses.PUT, api_url("/core/relationships/2"), json={"id": 2})
        result = mgr.reorder(PUB, "isAuthorOfPublication", [P2, PERSON1])
        assert [r["item_uuid"] for r in result] == [P2, P1]
        puts = [json.loads(c.request.body) for c in http.calls if c.request.method == "PUT"]
        assert puts == [{"leftPlace": 1, "rightPlace": 0, "leftwardValue": None, "rightwardValue": None}]

    def test_reorder_right_side_and_unknown(self, mgr, http, api_url, hal_page):
        by_label = api_url("/core/relationships/search/byLabel")
        rels = [relationship(1, P1, PUB, right_place=1, type_=PERSON_ORG | {"rightwardType": "x"}),
                relationship(2, P2, PUB, right_place=0, type_=PERSON_ORG | {"rightwardType": "x"})]
        http.add(responses.GET, by_label, json=hal_page("relationships", rels))
        http.add(responses.GET, api_url("/core/relationships/1"), json=rels[0])
        http.add(responses.PUT, api_url("/core/relationships/1"), json={"id": 1})
        mgr.reorder(PUB, "x", [P1])
        assert json.loads(last(http, "PUT").body)["rightPlace"] == 0
        with pytest.raises(ValueError, match="Not related"):
            mgr.reorder(PUB, "x", [ORG])

    def test_reorder_nothing_to_do(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/relationships/search/byLabel"),
                 json=hal_page("relationships", [relationship(1, PUB, P1, left_place=0)]))
        assert mgr.reorder(PUB, "isAuthorOfPublication", [P1])[0]["item_uuid"] == P1
        assert not [c for c in http.calls if c.request.method == "PUT"]


def test_errors_propagate(mgr, http, api_url):
    http.add(responses.GET, api_url("/core/relationships/404"), status=404)
    http.add(responses.POST, api_url("/core/relationships"), status=500)
    with pytest.raises(NotFoundError):
        mgr.get_relationship(404)
    with pytest.raises(ServerError):
        mgr.create_relationship(1, PUB, P1)
