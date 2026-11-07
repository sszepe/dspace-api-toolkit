# -*- coding: UTF-8 -*-
"""
Relationships between items (configurable entities, DSpace 7+)

Plain DSpace links entity items - Publication, Person, OrgUnit, Project,
Journal, JournalVolume, JournalIssue - through relationships of a
relationship type. DSpace-CRIS links entities through authority metadata
instead and normally has no relationship types; these endpoints are there for
completeness and plain-DSpace installations.

    GET    /core/relationshiptypes[/{id}]
    GET    /core/relationshiptypes/search/byEntityType?type=
    GET    /core/entitytypes/{id}/relationshiptypes          (EntityTypeManager)
    GET    /core/relationships[/{id}[/leftItem|/rightItem]]
    GET    /core/relationships/search/byLabel?label=&dso=&relatedEntityType=
    GET    /core/relationships/search/byItemsAndType?typeId=&relationshipLabel=&focusItem=&relatedItem=..
    GET    /core/items/{uuid}/relationships
    POST   /core/relationships?relationshipType=&leftwardValue=&rightwardValue=   text/uri-list: left, right
    PUT    /core/relationships/{id}                            places and name variants (full replace)
    PUT    /core/relationships/{id}/leftItem|rightItem         text/uri-list
    DELETE /core/relationships/{id}?copyVirtualMetadata=all|left|right|configured

Direction
    A relationship type is defined left -> right, e.g. Publication (left) to
    Person (right) with leftwardType "isAuthorOfPublication" (the relation as
    seen from the left item) and rightwardType "isPublicationOfAuthor". The
    same label may exist for several entity pairs. relate() and friends take
    the label from the point of view of the *focus* item and work out type
    and order:

        rel = client.relationships
        rel.relate(publication, "isAuthorOfPublication", person, name_variant="Smith, J.")
        rel.related_items(publication, "isAuthorOfPublication")      # authors in order
        rel.reorder(publication, "isAuthorOfPublication", [p2, p1])
        rel.unrelate(publication, "isAuthorOfPublication", person, copy_virtual_metadata="left")

Places and name variants
    leftPlace is the position of the relationship in the left item's list
    (e.g. author order on the publication), rightPlace in the right item's.
    The name variant (leftwardValue / rightwardValue) is how the related
    item is shown on that side ("Smith, J." instead of "Smith, Jane").

Virtual metadata
    Related items show up in the item's metadata as virtual values (e.g.
    dc.contributor.author with authority "virtual::<relationship id>").
    They cannot be changed with a metadata PATCH - change the relationship.
"""

import logging
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from ._base import BaseManager
from .exceptions import DSpaceError
from .projection import EmbedArg

# Logger for the module
logger = logging.getLogger(__name__)

# API endpoints and constants for relationships management
RELATIONSHIPS = "/core/relationships"
RELATIONSHIP_TYPES = "/core/relationshiptypes"
ITEMS = "/core/items"

# Copy virtual metadata options and sides for relationships
COPY_VIRTUAL_METADATA = ("all", "left", "right", "configured")
SIDES = ("left", "right")

# Type alias for item references (either a UUID string or an item dict)
ItemRef = Union[str, Dict[str, Any]]
_UNSET = object()


def entity_type_of(item: Dict[str, Any]) -> Optional[str]:
    """Entity type label of an item dict (entityType attribute or dspace.entity.type metadata).
    
    Parameters:
        item (dict): The item dictionary to extract the entity type from

    Returns:
        Optional[str]: The entity type label if available, otherwise None
    """
    if item.get("entityType"):
        return item["entityType"]
    values = (item.get("metadata") or {}).get("dspace.entity.type") or []
    return values[0].get("value") if values else None



# UUID regex for item hrefs
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _uuid_from_href(href: Optional[str]) -> Optional[str]:
    """'UUID' extraction from item hrefs.

    '.../core/items/<uuid>' -> '<uuid>' (None if the link has no item uuid at the end).

    Parameters:
        href (Optional[str]): The item href to extract the UUID from

    Returns:
        Optional[str]: The extracted UUID if present, otherwise None
    """
    if not href:
        return None
    last = href.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return last if _UUID_RE.fullmatch(last) else None

class RelationshipManager(BaseManager):
    """Relationship types and relationships between entity items"""

    def __init__(self, auth):
        """Initialize the RelationshipManager with the given authentication object.

        Parameters:
            auth: The authentication object to use for API requests
        """
        super().__init__(auth)
        self._types: Optional[List[Dict[str, Any]]] = None
        self._type_labels: Dict[Any, Tuple[Optional[str], Optional[str]]] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _item_uuid(self, item: ItemRef) -> str:
        """Get the UUID of an item reference.

        Parameters:
            item (ItemRef): The item reference, either a UUID string or an item dictionary

        Returns:
            str: The UUID of the item

        Raises:
            ValueError: If the item reference does not contain a valid UUID
        """
        uuid = self._uuid_of(item)
        if not uuid:
            raise ValueError(f"Not an item uuid or item dict: {item!r}")
        return uuid

    def _item_uri(self, item: ItemRef) -> str:
        """Get the URI of an item reference.

        Parameters:
            item (ItemRef): The item reference, either a UUID string or an item dictionary

        Returns:
            str: The URI of the item
        """
        return self._url(ITEMS, f"/{self._item_uuid(item)}")

    def _entity_type(self, item: ItemRef) -> Optional[str]:
        """Get the entity type label of an item reference.

        Parameters:
            item (ItemRef): The item reference, either a UUID string or an item dictionary

        Returns:
            Optional[str]: The entity type label if available, otherwise None
        """
        if isinstance(item, dict) and entity_type_of(item):
            return entity_type_of(item)
        data = self._get_json(self._url(ITEMS, f"/{self._item_uuid(item)}"), "Get item entity type")
        return entity_type_of(data)

    # ------------------------------------------------------------------
    # Relationship types
    # ------------------------------------------------------------------

    def get_relationship_types(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """All relationship types (cached per manager), with leftType / rightType embedded where the server does.

        Parameters:
            refresh (bool): If True, refresh the cached relationship types from the server

        Returns:
            List[Dict[str, Any]]: A list of relationship type dictionaries
        """
        if self._types is None or refresh:
            self._types = list(self._iter_embedded(self._url(RELATIONSHIP_TYPES), "relationshiptypes",
                                                   "List relationship types", embed=["leftType", "rightType"]))
        return self._types

    def get_relationship_type(self, type_id) -> Dict[str, Any]:
        """Get a specific relationship type by its ID.

        Parameters:
            type_id: The ID of the relationship type to retrieve

        Returns:
            Dict[str, Any]: The relationship type dictionary
        """
        return self._get_json(self._url(RELATIONSHIP_TYPES, f"/{type_id}"), f"Get relationship type {type_id}",
                              embed=["leftType", "rightType"])

    def get_relationship_types_for_entity(self, entity_type: str) -> List[Dict[str, Any]]:
        """Get relationship types with this entity type (label) on either side.

        Parameters:
            entity_type (str): The entity type label to filter relationship types by

        Returns:
            List[Dict[str, Any]]: A list of relationship type dictionaries matching the entity type
        """
        url = self._url(RELATIONSHIP_TYPES, "/search/byEntityType")
        return list(self._iter_embedded(url, "relationshiptypes", f"Relationship types of {entity_type}",
                                        [("type", entity_type)], embed=["leftType", "rightType"]))

    def type_entity_labels(self, rtype: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """Get the entity type labels for the left and right sides of a relationship type.

        Parameters:
            rtype (Dict[str, Any]): The relationship type dictionary

        Returns:
            Tuple[Optional[str], Optional[str]]: A tuple containing the left and right entity type labels, or None if not available
        """
        key = rtype.get("id")
        if key not in self._type_labels:
            embedded = rtype.get("_embedded") or {}
            labels = []
            for side in ("leftType", "rightType"):
                label = (embedded.get(side) or {}).get("label")
                href = ((rtype.get("_links") or {}).get(side) or {}).get("href")
                if label is None and href:
                    label = self.get_by_href(href).get("label")
                labels.append(label)
            self._type_labels[key] = (labels[0], labels[1])
        return self._type_labels[key]

    def resolve_type(self, label: str, focus_entity_type: Optional[str] = None,
                     related_entity_type: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
        """
        (relationship type, focus side) for a relation label seen from the
        focus item. focus side is "left" when label is the type's
        leftwardType, "right" when it is the rightwardType. Entity types
        disambiguate labels used by several types; ValueError if it stays
        ambiguous or nothing matches.

        Parameters:
            label (str): The relationship label to resolve
            focus_entity_type (Optional[str]): The entity type label of the focus item
            related_entity_type (Optional[str]): The entity type label of the related item

        Returns:
            Tuple[Dict[str, Any], str]: A tuple containing the resolved relationship type dictionary and the focus side ("left" or "right")

        Raises:
            ValueError: If the label is ambiguous or no matching relationship type is found 
        """
        candidates = []
        for rtype in self.get_relationship_types():
            for side, name in (("left", rtype.get("leftwardType")), ("right", rtype.get("rightwardType"))):
                if name != label:
                    continue
                left, right = self.type_entity_labels(rtype)
                focus, related = (left, right) if side == "left" else (right, left)
                if focus_entity_type and focus and focus != focus_entity_type:
                    continue
                if related_entity_type and related and related != related_entity_type:
                    continue
                candidates.append((rtype, side))
        if not candidates:
            raise ValueError(f"No relationship type with label {label!r}"
                             + (f" for {focus_entity_type} -> {related_entity_type}"
                                if focus_entity_type or related_entity_type else "")
                             + " (is the entity model loaded? dspace initialize-entities)")
        if len(candidates) > 1:
            pairs = [self.type_entity_labels(t) for t, _ in candidates]
            raise ValueError(f"Label {label!r} is ambiguous ({pairs}) - pass the entity types or type_id")
        return candidates[0]

    def _resolve(self, focus: ItemRef, label: str, related: Optional[ItemRef], type_id=None
                 ) -> Tuple[Dict[str, Any], str]:
        """Resolve a relationship type based on the focus item, label, and related item.

        Parameters:
            focus (ItemRef): The focus item of the relationship
            label (str): The relationship label to resolve
            related (Optional[ItemRef]): The related item of the relationship
            type_id (Optional[str], optional): The ID of the relationship type to resolve

        Returns:
            Tuple[Dict[str, Any], str]: A tuple containing the resolved relationship type dictionary and the focus side ("left" or "right")

        Raises:
            ValueError: If the label is ambiguous or no matching relationship type is found
        """
        if type_id is not None:
            rtype = self.get_relationship_type(type_id)
            if label == rtype.get("leftwardType"):
                return rtype, "left"
            if label == rtype.get("rightwardType"):
                return rtype, "right"
            raise ValueError(f"Relationship type {type_id} has no label {label!r}")
        return self.resolve_type(label, self._entity_type(focus),
                                 self._entity_type(related) if related is not None else None)

    # ------------------------------------------------------------------
    # Reading relationships
    # ------------------------------------------------------------------

    def iter_relationships(self, embed: EmbedArg = "relationshipType", page_size: Optional[int] = None,
                           max_results: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Every relationship in the repository (a generator) - e.g. for audits or exports.

        Parameters:
            embed (EmbedArg, optional): The embed parameter for the request
            page_size (Optional[int], optional): The number of items per page
            max_results (Optional[int], optional): The maximum number of results to retrieve

        Returns:
            Iterator[Dict[str, Any]]: An iterator over relationship dictionaries
        """
        return self._iter_embedded(self._url(RELATIONSHIPS), "relationships", "List relationships",
                                   page_size=page_size, max_results=max_results, embed=embed)

    def get_relationship(self, relationship_id, embed: EmbedArg = None) -> Dict[str, Any]:
        """Get a specific relationship by its ID.

        Parameters:
            relationship_id: The ID of the relationship to retrieve
            embed (EmbedArg, optional): The embed parameter for the request

        Returns:
            Dict[str, Any]: The relationship dictionary
        """
        return self._get_json(self._url(RELATIONSHIPS, f"/{relationship_id}"),
                              f"Get relationship {relationship_id}", embed=embed)

    def get_relationship_item(self, relationship, side: str) -> Dict[str, Any]:
        """Get the left or right item of a relationship.

        Parameters:
            relationship (Union[int, Dict[str, Any]]): The relationship ID or dictionary
            side (str): "left" or "right" to specify which item to retrieve

        Returns:
            Dict[str, Any]: The item dictionary

        Raises:
            ValueError: If the side is not "left" or "right", or if the relationship has no link for the specified side

        Notes:
            DSpace links the item directly (_links.leftItem -> /core/items/{uuid});
            there is no /relationships/{id}/leftItem resource to GET.
        """
        if side not in SIDES:
            raise ValueError("side must be 'left' or 'right'")
        if not isinstance(relationship, dict):
            relationship = self.get_relationship(relationship)
        href = ((relationship.get("_links") or {}).get(f"{side}Item") or {}).get("href")
        if not href:
            raise ValueError(f"Relationship {relationship.get('id')} has no {side}Item link")
        return self._get_json(href, f"Get {side} item of relationship {relationship.get('id')}")

    def ends(self, relationship: Dict[str, Any]) -> Tuple[str, str]:
        """
        Get the UUIDs of the left and right items of a relationship.

        Parameters:
            relationship (Dict[str, Any]): The relationship dictionary

        Returns:
            Tuple[str, str]: A tuple containing the UUIDs of the left and right items (left, right)

        Notes:
            The UUIDs are retrieved from leftId/rightId, embedded items, or the item links.
        """
        embedded = relationship.get("_embedded") or {}
        links = relationship.get("_links") or {}
        result = []
        for side in SIDES:
            uuid = relationship.get(f"{side}Id") or (embedded.get(f"{side}Item") or {}).get("uuid")
            if not uuid:
                uuid = _uuid_from_href((links.get(f"{side}Item") or {}).get("href"))
            if not uuid:
                uuid = self.get_relationship_item(relationship, side).get("uuid")
            result.append(uuid)
        return result[0], result[1]

    def iter_item_relationships(self, item: ItemRef, embed: EmbedArg = "relationshipType"
                                ) -> Iterator[Dict[str, Any]]:
        """Every relationship with the item on either side (a generator).

        Parameters:
            item (ItemRef): The item to find relationships for
            embed (EmbedArg, optional): The embed parameter for the request

        Returns:
            Iterator[Dict[str, Any]]: An iterator over relationship dictionaries
        """
        uuid = self._item_uuid(item)
        return self._iter_embedded(self._url(ITEMS, f"/{uuid}/relationships"), "relationships",
                                   f"Get relationships of item {uuid}", embed=embed)

    def search_by_label(self, label: str, dso: Optional[ItemRef] = None,
                        related_entity_type: Optional[str] = None,
                        max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """Relationships of a type label (either side), optionally of one item and related entity type.

        Parameters:
            label (str): The relationship label to search for
            dso (Optional[ItemRef], optional): The item to filter relationships by
            related_entity_type (Optional[str], optional): The related entity type to filter by
            max_results (Optional[int], optional): The maximum number of results to retrieve

        Returns:
            List[Dict[str, Any]]: A list of relationship dictionaries
        """
        params = [("label", label)]
        if dso is not None:
            params.append(("dso", self._item_uuid(dso)))
        if related_entity_type:
            params.append(("relatedEntityType", related_entity_type))
        return list(self._iter_embedded(self._url(RELATIONSHIPS, "/search/byLabel"), "relationships",
                                        f"Search relationships {label}", params, max_results=max_results))

    def find_relationships(self, focus: ItemRef, label: str, related: Union[ItemRef, Iterable[ItemRef]],
                           type_id=None) -> List[Dict[str, Any]]:
        """Existing relationships of the label between focus and the given related item(s).

        Parameters:
            focus (ItemRef): The focus item of the relationship
            label (str): The relationship label to search for
            related (Union[ItemRef, Iterable[ItemRef]]): The related item(s) to search for
            type_id (Optional[str], optional): The ID of the relationship type to filter by

        Returns:
            List[Dict[str, Any]]: A list of relationship dictionaries
        Notes:
            The function will return an empty list if no related items are provided.
        """
        related_list = [related] if isinstance(related, (str, dict)) else list(related)
        if not related_list:
            return []
        rtype, _side = self._resolve(focus, label, related_list[0], type_id)
        params = [("typeId", rtype["id"]), ("relationshipLabel", label), ("focusItem", self._item_uuid(focus))]
        params += [("relatedItem", self._item_uuid(r)) for r in related_list]
        return list(self._iter_embedded(self._url(RELATIONSHIPS, "/search/byItemsAndType"), "relationships",
                                        "Find relationships by items and type", params))

    def related_items(self, item: ItemRef, label: str) -> List[Dict[str, Any]]:
        """
        Retrieve items related to the given item under the specified relationship label.

        Parameters:
            item (ItemRef): The item to find related items for
            label (str): The relationship label to search for

        Returns:
            List[Dict[str, Any]]: A list of dictionaries containing related item information, including relationship ID, item UUID, place, name variant, and type ID.   

        Items related to `item` under `label` (seen from `item`), in order:
        [{"relationship_id", "item_uuid", "place", "name_variant", "type_id"}].
        """
        uuid = self._item_uuid(item)
        result = []
        for relationship in self.search_by_label(label, dso=uuid):
            rtype = (relationship.get("_embedded") or {}).get("relationshipType") or {}
            left, right = self.ends(relationship)
            if left == uuid and rtype.get("leftwardType", label) == label:
                other, place, variant = right, relationship.get("leftPlace"), relationship.get("leftwardValue")
            elif right == uuid and rtype.get("rightwardType", label) == label:
                other, place, variant = left, relationship.get("rightPlace"), relationship.get("rightwardValue")
            else:
                continue
            result.append({"relationship_id": relationship["id"], "item_uuid": other, "place": place,
                           "name_variant": variant, "type_id": rtype.get("id")})
        return sorted(result, key=lambda r: (r["place"] is None, r["place"]))

    # ------------------------------------------------------------------
    # Writing relationships
    # ------------------------------------------------------------------

    def create_relationship(self, type_id, left_item: ItemRef, right_item: ItemRef,
                            leftward_value: Optional[str] = None,
                            rightward_value: Optional[str] = None) -> Dict[str, Any]:
        """Create a relationship; left/right must match the type's leftType/rightType.
        
        Parameters:
            type_id (str): The ID of the relationship type
            left_item (ItemRef): The left item of the relationship
            right_item (ItemRef): The right item of the relationship
            leftward_value (Optional[str], optional): The leftward value for the relationship
            rightward_value (Optional[str], optional): The rightward value for the relationship

        Returns:
            Dict[str, Any]: The created relationship dictionary
        """
        params = [("relationshipType", type_id)]
        if leftward_value is not None:
            params.append(("leftwardValue", leftward_value))
        if rightward_value is not None:
            params.append(("rightwardValue", rightward_value))
        body = f"{self._item_uri(left_item)}\n{self._item_uri(right_item)}"
        relationship = self._request("POST", self._url(RELATIONSHIPS), "Create relationship", expected=(200, 201),
                                     headers=self._headers("text/uri-list"), params=params, data=body).json()
        logger.info("Created relationship %s (type %s)", relationship.get("id"), type_id)
        return relationship

    def relate(self, focus: ItemRef, label: str, related: ItemRef, name_variant: Optional[str] = None,
               type_id=None) -> Dict[str, Any]:
        """
        Relate focus to related under `label` as seen from focus, e.g.
        relate(publication, "isAuthorOfPublication", person). name_variant is
        how `related` appears on the focus item.

        Parameters:
            focus (ItemRef): The focus item of the relationship
            label (str): The relationship label to use
            related (ItemRef): The related item
            name_variant (Optional[str], optional): The name variant for the related item as seen from the focus item
            type_id (Optional[str], optional): The ID of the relationship type to use

        Returns:
            Dict[str, Any]: The created relationship dictionary
        """
        rtype, side = self._resolve(focus, label, related, type_id)
        if side == "left":
            return self.create_relationship(rtype["id"], focus, related, leftward_value=name_variant)
        return self.create_relationship(rtype["id"], related, focus, rightward_value=name_variant)

    def ensure_related(self, focus: ItemRef, label: str, related: ItemRef, name_variant: Optional[str] = None,
                       type_id=None) -> Tuple[Dict[str, Any], bool]:
        """Like relate(), but returns (existing relationship, False) when it is already there.
        
        Parameters:
            focus (ItemRef): The focus item of the relationship
            label (str): The relationship label to use
            related (ItemRef): The related item
            name_variant (Optional[str], optional): The name variant for the related item as seen from the focus item
            type_id (Optional[str], optional): The ID of the relationship type to use

        Returns:
            Tuple[Dict[str, Any], bool]: A tuple containing the relationship dictionary and a boolean indicating if it was newly created
        """
        existing = self.find_relationships(focus, label, related, type_id)
        if existing:
            return existing[0], False
        return self.relate(focus, label, related, name_variant, type_id), True

    def update_relationship(self, relationship_id, left_place=_UNSET, right_place=_UNSET,
                            leftward_value=_UNSET, rightward_value=_UNSET) -> Dict[str, Any]:
        """
        Change places and/or name variants. The PUT replaces all four
        properties, so the current values are read first and only the given
        ones change (pass None to clear a name variant).

        Parameters:
            relationship_id (str): The ID of the relationship to update
            left_place (Optional[int], optional): The new left place value
            right_place (Optional[int], optional): The new right place value
            leftward_value (Optional[str], optional): The new leftward value
            rightward_value (Optional[str], optional): The new rightward value

        Returns:
            Dict[str, Any]: The updated relationship dictionary
        """
        current = self.get_relationship(relationship_id)
        body = {key: current.get(key) for key in ("leftPlace", "rightPlace", "leftwardValue", "rightwardValue")}
        for key, value in (("leftPlace", left_place), ("rightPlace", right_place),
                           ("leftwardValue", leftward_value), ("rightwardValue", rightward_value)):
            if value is not _UNSET:
                body[key] = value
        return self._request("PUT", self._url(RELATIONSHIPS, f"/{relationship_id}"),
                             f"Update relationship {relationship_id}", headers=self._headers(), json=body).json()

    def set_name_variant(self, relationship_id, side: str, value: Optional[str]) -> Dict[str, Any]:
        """Name variant on the left or right item (None removes it).
        
        Parameters:
            relationship_id (str): The ID of the relationship to update
            side (str): Either "left" or "right" indicating which side's name variant to set
            value (Optional[str]): The new name variant value (None removes it)

        Returns:
            Dict[str, Any]: The updated relationship dictionary
        """
        if side not in SIDES:
            raise ValueError("side must be 'left' or 'right'")
        return self.update_relationship(relationship_id, **{f"{side}ward_value": value})

    def replace_item(self, relationship_id, side: str, item: ItemRef) -> Dict[str, Any]:
        """Point the left or right end of a relationship to another item (the type stays)."""
        if side not in SIDES:
            raise ValueError("side must be 'left' or 'right'")
        response = self._request("PUT", self._url(RELATIONSHIPS, f"/{relationship_id}/{side}Item"),
                                 f"Replace {side} item of relationship {relationship_id}", expected=(200, 204),
                                 headers=self._headers("text/uri-list"), data=self._item_uri(item))
        return self._json_or_none(response) or self.get_relationship(relationship_id)

    def delete_relationship(self, relationship_id, copy_virtual_metadata: Optional[str] = None) -> bool:
        """
        Delete a relationship.

        Parameters:
            relationship_id (str): The ID of the relationship to delete
            copy_virtual_metadata (Optional[str], optional): Keeps the related values as real metadata: "all", "left", "right" or "configured"

        Returns:
            bool: True if the relationship was successfully deleted
        """
        if copy_virtual_metadata is not None and copy_virtual_metadata not in COPY_VIRTUAL_METADATA:
            raise ValueError(f"copy_virtual_metadata must be one of {COPY_VIRTUAL_METADATA}")
        params = {"copyVirtualMetadata": copy_virtual_metadata} if copy_virtual_metadata else None
        self._request("DELETE", self._url(RELATIONSHIPS, f"/{relationship_id}"),
                      f"Delete relationship {relationship_id}", expected=(204,), params=params)
        logger.info("Deleted relationship %s", relationship_id)
        return True

    def unrelate(self, focus: ItemRef, label: str, related: ItemRef, copy_virtual_metadata: Optional[str] = None,
                 type_id=None) -> int:
        """Delete the relationships of the label between the two items; returns how many.
        
        Parameters:
            focus (ItemRef): The focus item of the relationship
            label (str): The relationship label to use
            related (ItemRef): The related item
            copy_virtual_metadata (Optional[str], optional): Keeps the related values as real metadata: "all", "left", "right" or "configured"
            type_id (Optional[str], optional): The ID of the relationship type to use

        Returns:
            int: The number of relationships deleted
        """
        found = self.find_relationships(focus, label, related, type_id)
        for relationship in found:
            self.delete_relationship(relationship["id"], copy_virtual_metadata)
        return len(found)

    def reorder(self, item: ItemRef, label: str, ordered: Iterable[ItemRef]) -> List[Dict[str, Any]]:
        """
        Put the related items of `item` under `label` into this order (e.g.
        author order). Items not listed keep their relative order after the
        listed ones. DSpace shifts the other places when one place changes,
        so places are set one by one from the front.

        Parameters:
            item (ItemRef): The focus item whose related items are to be reordered
            label (str): The relationship label to use
            ordered (Iterable[ItemRef]): The desired order of related items

        Returns:
            List[Dict[str, Any]]: The updated list of related items under the given label
        """
        current = self.related_items(item, label)
        by_item = {r["item_uuid"]: r for r in current}
        wanted = [self._item_uuid(o) for o in ordered]
        unknown = [u for u in wanted if u not in by_item]
        if unknown:
            raise ValueError(f"Not related to {self._item_uuid(item)} under {label!r}: {unknown}")
        uuid = self._item_uuid(item)
        # keep the server's numbering (0- or 1-based): start at the lowest current place
        base = min((r["place"] for r in current if r["place"] is not None), default=0)
        for place, related_uuid in enumerate(wanted, start=base):
            entry = by_item[related_uuid]
            if entry["place"] == place:
                continue
            relationship = self.get_relationship(entry["relationship_id"])
            left, _right = self.ends(relationship)
            key = "left_place" if left == uuid else "right_place"
            self.update_relationship(entry["relationship_id"], **{key: place})
            current = self.related_items(item, label)
            by_item = {r["item_uuid"]: r for r in current}
        return current

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def delete_item_relationships(self, item: ItemRef, copy_virtual_metadata: Optional[str] = None,
                                  label: Optional[str] = None) -> int:
        """Delete every relationship of an item (optionally of one label); returns how many.
        
        Parameters:
            item (ItemRef): The item whose relationships are to be deleted
            copy_virtual_metadata (Optional[str], optional): Keeps the related values as real metadata: "all", "left", "right" or "configured"
            label (Optional[str], optional): The relationship label to filter by

        Returns:
            int: The number of relationships deleted
        """
        relationships = self.search_by_label(label, dso=item) if label else list(self.iter_item_relationships(item))
        deleted = 0
        for relationship in relationships:
            try:
                self.delete_relationship(relationship["id"], copy_virtual_metadata)
                deleted += 1
            except DSpaceError as exc:
                logger.warning("Could not delete relationship %s: %s", relationship.get("id"), exc)
        return deleted
