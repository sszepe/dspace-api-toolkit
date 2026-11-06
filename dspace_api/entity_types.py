# -*- coding: UTF-8 -*-
"""
Entity types for DSpace API (DSpace 7+ / DSpace-CRIS 7+)

Mostly used to gate "create" actions in a UI or script: which entity types
can the current user submit, and into which collections.

    GET /core/entitytypes[/{id}]
    GET /core/entitytypes/label/{label}
    GET /core/entitytypes/{id}/relationshiptypes
    GET /core/entitytypes/search/findAllByAuthorizedCollection
    GET /core/entitytypes/search/findAllByAuthorizedExternalSource
    GET /core/collections/search/findSubmitAuthorizedByEntityType?entityType=
"""

from typing import Any, Dict, List, Optional, Set

from ._base import BaseManager
from .projection import EmbedArg, ProjectionArg
from .config import Endpoints

# Endpoints for entity types and collections
ENTITY_TYPES = getattr(Endpoints, "ENTITY_TYPES", "/core/entitytypes")
COLLECTIONS = getattr(Endpoints, "COLLECTIONS", "/core/collections")


class EntityTypeManager(BaseManager):
    """Entity type lookups and per-user submission authorization"""

    def get_entity_types(self) -> List[Dict[str, Any]]:
        """List all entity types.

        Returns:
            List[Dict[str, Any]]: A list of entity type data.
        """
        return list(self._iter_embedded(self._url(ENTITY_TYPES), "entitytypes", "List entity types"))

    def get_entity_type(self, entity_type_id) -> Dict[str, Any]:
        """Get an entity type by its ID.

        Parameters:
            entity_type_id (int): The ID of the entity type.

        Returns:
            Dict[str, Any]: The entity type data.
        """
        return self._get_json(self._url(ENTITY_TYPES, f"/{entity_type_id}"), f"Get entity type {entity_type_id}")

    def get_relationship_types(self, entity_type) -> List[Dict[str, Any]]:
        """Relationship types with this entity type (id, label or dict) on either side.

        Parameters:
            entity_type (Union[int, str, Dict[str, Any]]): The entity type to check.

        Returns:
            List[Dict[str, Any]]: A list of relationship type data.
        """
        if isinstance(entity_type, dict):
            type_id = entity_type["id"]
        elif isinstance(entity_type, int) or str(entity_type).isdigit():
            type_id = entity_type
        else:
            type_id = self.get_entity_type_by_label(entity_type)["id"]
        url = self._url(ENTITY_TYPES, f"/{type_id}/relationshiptypes")
        return list(self._iter_embedded(url, "relationshiptypes", f"Relationship types of entity type {type_id}"))

    def get_entity_type_by_label(self, label: str) -> Dict[str, Any]:
        """Get an entity type by its label.

        Parameters:
            label (str): The label of the entity type.

        Returns:
            Dict[str, Any]: The entity type data.
        """
        return self._get_json(self._url(ENTITY_TYPES, f"/label/{label}"), f"Get entity type {label}")

    def get_authorized_collection_entity_types(self) -> List[Dict[str, Any]]:
        """Entity types the current user can submit (submit right on at least one collection of that type).
        
        Returns:
            List[Dict[str, Any]]: A list of entity type data.
        """
        url = self._url(ENTITY_TYPES, "/search/findAllByAuthorizedCollection")
        return list(self._iter_embedded(url, "entitytypes", "Find entity types by authorized collection"))

    def get_authorized_external_source_entity_types(self) -> List[Dict[str, Any]]:
        """Entity types the current user can import from an external source (ORCID, Crossref, ...).

        Returns:
            List[Dict[str, Any]]: A list of entity type data.
        """
        url = self._url(ENTITY_TYPES, "/search/findAllByAuthorizedExternalSource")
        return list(self._iter_embedded(url, "entitytypes", "Find entity types by authorized external source"))

    def can_submit(self, entity_type: str) -> bool:
        """True if the current user may create items of this type somewhere."""
        return entity_type in self.authorized_labels()

    def authorized_labels(self) -> Set[str]:
        """Get the labels of entity types the current user can submit to.

        Returns:
            Set[str]: A set of entity type labels.
        """
        return {t.get("label") for t in self.get_authorized_collection_entity_types()}

    def get_submit_collections(self, entity_type: str, query: Optional[str] = None,
                               max_results: Optional[int] = None,
                               all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Collections of this entity type the current user can submit to.

        Parameters:
            entity_type (str): The entity type label.
            query (Optional[str], optional): Search query for collections. Defaults to None.
            max_results (Optional[int], optional): Maximum number of results to return. Defaults to None.
            all_languages (Optional[bool], optional): Whether to include all languages. Defaults to None.
            projection (ProjectionArg, optional): Projection to apply. Defaults to None.
            embed (EmbedArg, optional): Embed options. Defaults to None.

        Returns:
            List[Dict[str, Any]]: A list of collection data.
        """
        params = [("entityType", entity_type)] + ([("query", query)] if query else [])
        url = self._url(COLLECTIONS, "/search/findSubmitAuthorizedByEntityType")
        return list(self._iter_embedded(url, "collections", f"Find {entity_type} submit collections", params,
                                        max_results=max_results, all_languages=all_languages, projection=projection, embed=embed))
