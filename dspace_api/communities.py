# -*- coding: UTF-8 -*-
"""
Community management for DSpace API (DSpace 7+ / DSpace-CRIS 7+)

Covered REST contract
    GET    /core/communities/{uuid}
    GET    /core/communities/search/top
    GET    /core/communities/{uuid}/subcommunities
    GET    /core/communities/{uuid}/collections
    POST   /core/communities                         top-level community
    POST   /core/communities?parent={uuid}           subcommunity
    PATCH  /core/communities/{uuid}                  metadata
    DELETE /core/communities/{uuid}                  (deletes everything below it!)
    GET|POST|DELETE /core/communities/{uuid}/adminGroup
    GET    /discover/search/objects?configuration=editCommunity&dsoType=COMMUNITY
    GET    /core/communities/search/findAdminAuthorized|findEditAuthorized|findAddAuthorized
    GET|POST /core/communities/{uuid}/logo, DELETE via the logo bitstream
    GET    /core/communities/{uuid}/parentCommunity

Shared with collections (see _containers.py): get/set/delete_logo,
get_parent_community, get_ancestors, patch_metadata, find_admin_authorized,
find_edit_authorized. iter_tree() walks a whole community hierarchy.
"""

import logging
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from ._base import BaseManager
from ._containers import ContainerMixin
from .projection import EmbedArg, ProjectionArg
from .config import Endpoints

# Logger for the module
logger = logging.getLogger(__name__)

# API endpoint for communities
COMMUNITIES = getattr(Endpoints, "COMMUNITIES", "/core/communities")

# Type alias for a reference to a DSpace object (UUID or full object)
Ref = Union[str, Dict[str, Any]]


def build_dso_metadata(title: str, description: Optional[str] = None, abstract: Optional[str] = None,
                       rights: Optional[str] = None, table_of_contents: Optional[str] = None,
                       language: Optional[str] = None, **extra: Any) -> Dict[str, List[Dict[str, Any]]]:
    """
    Metadata for a community/collection form. Empty or whitespace-only
    values are left out. Fields: dc.title, dc.description (HTML allowed),
    dc.description.abstract, dc.rights (HTML allowed),
    dc.description.tableofcontents; extra={"dc.rights.license": "..."}.
    Booleans become "true"/"false".

    Parameters:
        title (str): Title of the community/collection
        description (Optional[str]): Description (HTML allowed)
        abstract (Optional[str]): Abstract
        rights (Optional[str]): Rights (HTML allowed)
        table_of_contents (Optional[str]): Table of contents
        language (Optional[str]): Language code for the metadata values
        **extra: Additional metadata fields

    Returns:
        Dict[str, List[Dict[str, Any]]]: Metadata dictionary suitable for DSpace API
    """
    if not (title or "").strip():
        raise ValueError("title is required")
    fields = {
        "dc.title": title,
        "dc.description": description,
        "dc.description.abstract": abstract,
        "dc.rights": rights,
        "dc.description.tableofcontents": table_of_contents,
        **extra,
    }
    md = {}
    for field, value in fields.items():
        if value is None:
            continue
        value = ("true" if value else "false") if isinstance(value, bool) else str(value).strip()
        if value:
            md[field] = [{"value": value, "language": language}]
    return md


class CommunityManager(ContainerMixin, BaseManager):
    """Manages communities, their administrator group, logo and hierarchy"""

    # Container configuration for communities
    _CONTAINER_PATH = COMMUNITIES
    _CONTAINER_KEY = "communities"

    def __init__(self, auth):
        """Initialize the CommunityManager with authentication.

        Parameters:
            auth: Authentication object for API access

        """
        super().__init__(auth)
        self._group_manager = None

    @property
    def groups(self):
        """Access the GroupManager for community groups.

        Returns:
            GroupManager: Manager for community groups
        """
        if self._group_manager is None:
            from .groups import GroupManager
            self._group_manager = GroupManager(self.auth)
        return self._group_manager

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_community(self, community_uuid: str, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Dict[str, Any]:
        """Get a community by UUID.

        Parameters:
            community_uuid (str): UUID of the community
            all_languages (Optional[bool]): Whether to include all languages
            projection (ProjectionArg): Projection for the response
            embed (EmbedArg): Embedded resources to include

        Returns:
            Dict[str, Any]: Community details
        """
        return self._get_json(self._url(COMMUNITIES, f"/{community_uuid}"), f"Get community {community_uuid}",
                              all_languages=all_languages, projection=projection, embed=embed)

    def get_top_communities(self, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Get the top-level communities.

        Parameters:
            all_languages (Optional[bool]): Whether to include all languages
            projection (ProjectionArg): Projection for the response
            embed (EmbedArg): Embedded resources to include

        Returns:
            List[Dict[str, Any]]: List of top-level communities
        """
        return list(self._iter_embedded(self._url(COMMUNITIES, "/search/top"), "communities", "Get top communities",
                                        all_languages=all_languages, projection=projection, embed=embed))

    def get_subcommunities(self, community_uuid: str, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Get subcommunities of a community.

        Parameters:
            community_uuid (str): UUID of the community
            all_languages (Optional[bool]): Whether to include all languages
            projection (ProjectionArg): Projection for the response
            embed (EmbedArg): Embedded resources to include

        Returns:
            List[Dict[str, Any]]: List of subcommunities
        """
        url = self._url(COMMUNITIES, f"/{community_uuid}/subcommunities")
        return list(self._iter_embedded(url, ("subcommunities", "communities"),
                                        f"Get subcommunities of {community_uuid}", all_languages=all_languages, projection=projection, embed=embed))

    def get_community_collections(self, community_uuid: str, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Get collections of a community.

        Parameters:
            community_uuid (str): UUID of the community
            all_languages (Optional[bool]): Whether to include all languages
            projection (ProjectionArg): Projection for the response
            embed (EmbedArg): Embedded resources to include

        Returns:
            List[Dict[str, Any]]: List of collections
        """
        url = self._url(COMMUNITIES, f"/{community_uuid}/collections")
        return list(self._iter_embedded(url, ("collections", "mappedCollections"),
                                        f"Get collections of {community_uuid}", all_languages=all_languages, projection=projection, embed=embed))

    def search_editable_communities(self, query: str = "", configuration: str = "editCommunity",
                                    max_results: Optional[int] = 20,
                                    all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Communities (top-level or sub) the current user can edit, e.g. to pick a parent.
        
        Parameters:
            query (str): Search query
            configuration (str): Search configuration
            max_results (Optional[int]): Maximum number of results
            all_languages (Optional[bool]): Whether to include all languages
            projection (ProjectionArg): Projection for the response
            embed (EmbedArg): Embedded resources to include

        Returns:
            List[Dict[str, Any]]: List of editable communities
        """
        return self._discover(query, dso_type="COMMUNITY", configuration=configuration, sort="dc.title,ASC",
                              max_results=max_results, all_languages=all_languages, projection=projection, embed=embed)

    def find_add_authorized(self, query: Optional[str] = None, max_results: Optional[int] = None,
                            embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Communities in which the current user may create subcommunities or collections.
        
        Parameters:
            query (Optional[str]): Search query
            max_results (Optional[int]): Maximum number of results
            embed (EmbedArg): Embedded resources to include

        Returns:
            List[Dict[str, Any]]: List of communities where the user can add subcommunities or collections
        """
        return self._authorized_search("findAddAuthorized", query, max_results=max_results, embed=embed)

    def iter_tree(self, community_uuid: Optional[str] = None, max_depth: Optional[int] = None,
                  include_collections: bool = True) -> Iterator[Tuple[int, str, Dict[str, Any]]]:
        """Iterate over the community tree in a depth-first manner.

        Parameters:
            community_uuid (Optional[str]): UUID of the starting community. If None, starts at top-level communities.
            max_depth (Optional[int]): Maximum depth to traverse
            include_collections (bool): Whether to include collections in the iteration

        Yields:
            Tuple[int, str, Dict[str, Any]]: (depth, "community"|"collection", object)
        """
        roots = [self.get_community(community_uuid)] if community_uuid else self.get_top_communities()
        stack = [(0, root) for root in reversed(roots)]
        while stack:
            depth, community = stack.pop()
            yield depth, "community", community
            if max_depth is not None and depth >= max_depth:
                continue
            if include_collections:
                for collection in self.get_community_collections(community["uuid"]):
                    yield depth + 1, "collection", collection
            for sub in reversed(self.get_subcommunities(community["uuid"])):
                stack.append((depth + 1, sub))

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create_community(self, metadata: Dict[str, Any], parent_uuid: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a community; with parent_uuid it becomes a subcommunity.
        metadata: e.g. build_dso_metadata("Faculty of Music", description="...")

        Parameters:
            metadata (Dict[str, Any]): Metadata for the community
            parent_uuid (Optional[str]): UUID of the parent community, if creating a subcommunity

        Returns:
            Dict[str, Any]: The created community object
        """
        md = self._format_metadata(metadata)
        if "dc.title" not in md:
            raise ValueError("metadata must contain dc.title")
        body = {"name": md["dc.title"][0]["value"], "metadata": md, "type": "community"}
        params = {"parent": parent_uuid} if parent_uuid else None
        community = self._request("POST", self._url(COMMUNITIES), "Create community", expected=(200, 201),
                                  headers=self._headers(), params=params, json=body).json()
        logger.info("Created %s %s", "subcommunity" if parent_uuid else "community", community.get("uuid"))
        return community

    def update_community_metadata(self, community_uuid: str, operation: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update metadata of a community.

        Parameters:
            community_uuid (str): UUID of the community to update
            operation (str): Patch operation (e.g., "add", "replace", "remove")
            metadata (Dict[str, Any]): Metadata to apply with the operation

        Returns:
            Dict[str, Any]: The updated community object
        """
        url = self._url(COMMUNITIES, f"/{community_uuid}")
        return self._request("PATCH", url, f"Update community {community_uuid}", headers=self._headers(),
                             json=self._metadata_patch_ops(operation, metadata)).json()

    def delete_community(self, community_uuid: str) -> bool:
        """
        Delete a community.

        Parameters:
            community_uuid (str): UUID of the community to delete

        Returns:
            bool: True if the community was successfully deleted

        WARNING: deletes all subcommunities, collections and items below it.
        """
        self._request("DELETE", self._url(COMMUNITIES, f"/{community_uuid}"),
                      f"Delete community {community_uuid}", expected=(204,))
        logger.info("Deleted community %s", community_uuid)
        return True

    # ------------------------------------------------------------------
    # Administrators
    # ------------------------------------------------------------------

    def get_admin_group(self, community_uuid: str) -> Optional[Dict[str, Any]]:
        """Get the admin group for a community.

        Parameters:
            community_uuid (str): UUID of the community

        Returns:
            Optional[Dict[str, Any]]: The admin group object if it exists, None otherwise.
        """
        return self.groups.get_role_group("community", community_uuid, "adminGroup")

    def get_or_create_admin_group(self, community_uuid: str, description: Optional[str] = None) -> Dict[str, Any]:
        """Get or create the admin group for a community.

        Parameters:
            community_uuid (str): UUID of the community
            description (Optional[str]): Description for the admin group if it needs to be created

        Returns:
            Dict[str, Any]: The admin group object
        """
        return self.groups.get_or_create_role_group("community", community_uuid, "adminGroup", description)

    def delete_admin_group(self, community_uuid: str) -> bool:
        """Delete the admin group for a community.

        Parameters:
            community_uuid (str): UUID of the community

        Returns:
            bool: True if the admin group was successfully deleted
        """
        return self.groups.delete_role_group("community", community_uuid, "adminGroup")

    def add_admins(self, community_uuid: str, epersons: Iterable[Ref] = (), subgroups: Iterable[Ref] = (),
                   description: Optional[str] = None) -> Dict[str, Any]:
        """Create the admin group if needed and add epersons and/or groups to it.

        Parameters:
            community_uuid (str): UUID of the community
            epersons (Iterable[Ref]): Epersons to add to the admin group
            subgroups (Iterable[Ref]): Subgroups to add to the admin group
            description (Optional[str]): Description for the admin group if it needs to be created

        Returns:
            Dict[str, Any]: The admin group object
        """
        return self.groups.ensure_role_members("community", community_uuid, "adminGroup",
                                               epersons, subgroups, description)

    def get_admins(self, community_uuid: str) -> Dict[str, List[Dict[str, Any]]]:
        """Get the members of the admin group for a community.

        Parameters:
            community_uuid (str): UUID of the community

        Returns:
            Dict[str, List[Dict[str, Any]]]: {"epersons": [...], "subgroups": [...]} of the admin group (empty if none).
        """
        group = self.get_admin_group(community_uuid)
        if not group:
            return {"epersons": [], "subgroups": []}
        return {"epersons": self.groups.get_group_epersons(group["uuid"]),
                "subgroups": self.groups.get_subgroups(group["uuid"])}
