# -*- coding: UTF-8 -*-
"""
EPerson group management for DSpace API (DSpace 7+ / DSpace-CRIS 7+)

Groups
    GET    /eperson/groups                              list
    POST   /eperson/groups                              create
    GET    /eperson/groups/{uuid}
    PATCH  /eperson/groups/{uuid}                       rename (/name), metadata
    DELETE /eperson/groups/{uuid}
    GET    /eperson/groups/{uuid}/object                linked community/collection (role groups)
    GET    /eperson/groups/search/byMetadata?query=
    GET    /eperson/groups/search/isNotMemberOf?group=&query=

Members
    GET|POST        /eperson/groups/{uuid}/subgroups    (POST body: text/uri-list)
    DELETE          /eperson/groups/{uuid}/subgroups/{subgroupUuid}
    GET|POST        /eperson/groups/{uuid}/epersons     (POST body: text/uri-list)
    DELETE          /eperson/groups/{uuid}/epersons/{epersonUuid}

Role groups of communities and collections
    GET|POST|DELETE /core/communities/{uuid}/adminGroup
    GET|POST|DELETE /core/collections/{uuid}/adminGroup
                    /core/collections/{uuid}/submittersGroup
                    /core/collections/{uuid}/itemReadGroup        default READ for new items
                    /core/collections/{uuid}/bitstreamReadGroup   default READ for new files
                    /core/collections/{uuid}/workflowGroups/{role} e.g. reviewer, editor, finaleditor

Groups and epersons can be referenced by UUID, by HAL href, by the object
dict the API returned, or by name (groups) / email (epersons).
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Union

from ._base import BaseManager
from .projection import EmbedArg, ProjectionArg
from .config import Endpoints
from .exceptions import NotFoundError

# Logger for the module
logger = logging.getLogger(__name__)

# API endpoints for DSpace groups and role groups
GROUPS = getattr(Endpoints, "GROUPS", "/eperson/groups")
EPERSONS = getattr(Endpoints, "EPERSONS", "/eperson/epersons")
COMMUNITIES = getattr(Endpoints, "COMMUNITIES", "/core/communities")
COLLECTIONS = getattr(Endpoints, "COLLECTIONS", "/core/collections")

# Role group endpoints per DSpace object type
ADMIN_GROUP = "adminGroup"
SUBMITTERS_GROUP = "submittersGroup"
ITEM_READ_GROUP = "itemReadGroup"
BITSTREAM_READ_GROUP = "bitstreamReadGroup"
WORKFLOW_GROUP_PREFIX = "workflowGroups/"

# Role groups for communities and collections
COMMUNITY_ROLES = (ADMIN_GROUP,)
COLLECTION_ROLES = (ADMIN_GROUP, SUBMITTERS_GROUP, ITEM_READ_GROUP, BITSTREAM_READ_GROUP)
_DSO_PATHS = {"community": COMMUNITIES, "collection": COLLECTIONS}

Ref = Union[str, Dict[str, Any]]


class GroupManager(BaseManager):
    """Manages DSpace groups, their members, and community/collection role groups"""

    def __init__(self, auth):
        """Initialize the GroupManager with authentication and configuration.

        Parameters:
            auth: Authentication object containing session and configuration.
        """
        super().__init__(auth)
        self._name_cache: Dict[str, str] = {}
        self._eperson_manager = None

    @property
    def epersons(self):
        """EPersonManager used to resolve eperson references given by email."""
        if self._eperson_manager is None:
            from .epersons import EPersonManager
            self._eperson_manager = EPersonManager(self.auth)
        return self._eperson_manager

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def get_group(self, group: Ref, embed_object: bool = False, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Dict[str, Any]:
        """Retrieve a group by its UUID or reference.

        Parameters:
            group (Ref): UUID or reference of the group.
            embed_object (bool): Whether to embed the full object.
            all_languages (Optional[bool]): Whether to include all languages.
            projection (ProjectionArg): Projection argument for the request.
            embed (EmbedArg): Embed argument for the request.

        Returns:
            Dict[str, Any]: The group details.
        """
        uuid = self.resolve_group(group)
        params = [("embed", "object")] if embed_object else None
        return self._get_json(self._url(GROUPS, f"/{uuid}"), f"Get group {uuid}", params, all_languages=all_languages, projection=projection, embed=embed)

    def iter_groups(self, page_size: Optional[int] = None, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None):
        """Every group in the repository (admin only). Can be a lot.

        Parameters:
            page_size (Optional[int]): Number of groups per page.
            all_languages (Optional[bool]): Whether to include all languages.
            projection (ProjectionArg): Projection argument for the request.
            embed (EmbedArg): Embed argument for the request.

        Returns:
            Iterator[Dict[str, Any]]: An iterator over the groups.
        """
        return self._iter_embedded(self._url(GROUPS), "groups", "List groups", page_size=page_size,
                                   all_languages=all_languages, projection=projection, embed=embed)

    def search_groups(self, query: str, max_results: Optional[int] = None,
                      embed_object: bool = False, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Search by name or exact UUID (byMetadata).
        
        Parameters:
            query (str): Search query for group names or exact UUID.
            max_results (Optional[int]): Maximum number of results to return.
            embed_object (bool): Whether to embed the full object.
            all_languages (Optional[bool]): Whether to include all languages.
            projection (ProjectionArg): Projection argument for the request.
            embed (EmbedArg): Embed argument for the request.

        Returns:
            List[Dict[str, Any]]: A list of groups matching the search query.
        """
        params = [("query", query)] + ([("embed", "object")] if embed_object else [])
        url = self._url(GROUPS, "/search/byMetadata")
        return list(self._iter_embedded(url, "groups", f"Search groups '{query}'", params, max_results=max_results,
                                        all_languages=all_languages, projection=projection, embed=embed))

    def search_groups_not_member_of(self, group: Ref, query: str = "", max_results: Optional[int] = None,
                                    embed_object: bool = True) -> List[Dict[str, Any]]:
        """Groups that could be added as subgroups of `group` (isNotMemberOf).

        Parameters:
            group (Ref): UUID or reference of the parent group.
            query (str): Search query for group names.
            max_results (Optional[int]): Maximum number of results to return.
            embed_object (bool): Whether to embed the full object.

        Returns:
            List[Dict[str, Any]]: A list of groups that can be added as subgroups.
        """
        params = [("group", self.resolve_group(group)), ("query", query)]
        if embed_object:
            params.append(("embed", "object"))
        url = self._url(GROUPS, "/search/isNotMemberOf")
        return list(self._iter_embedded(url, "groups", "Search addable groups", params, max_results=max_results))

    def find_group_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Exact (case-insensitive) name match, or None."""
        for group in self.search_groups(name):
            if (group.get("name") or "").lower() == name.lower():
                return group
        return None

    def resolve_group(self, group: Ref) -> str:
        """UUID for a group given as UUID, href, dict or name (names are cached).

        Parameters:
            group (Ref): UUID, href, dict, or name of the group.

        Returns:
            str: UUID of the group.
        """
        uuid = self._uuid_of(group)
        if uuid:
            return uuid
        if not isinstance(group, str) or not group:
            raise ValueError(f"Cannot resolve group reference {group!r}")
        key = group.lower()
        if key not in self._name_cache:
            found = self.find_group_by_name(group)
            if not found:
                raise NotFoundError(f"Group {group!r} not found")
            self._name_cache[key] = found["uuid"]
        return self._name_cache[key]

    def create_group(self, name: str, description: Optional[str] = None,
                     metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a standalone group (admin only). 422 if the name already exists.
        
        Parameters:
            name (str): Name of the group to create.
            description (Optional[str]): Description of the group.
            metadata (Optional[Dict[str, Any]]): Metadata for the group.

        Returns:
            Dict[str, Any]: The created group object.
        """
        md = dict(metadata or {})
        if description and "dc.description" not in md:
            md["dc.description"] = description
        body = {"name": name, "metadata": self._format_metadata(md)}
        group = self._request("POST", self._url(GROUPS), f"Create group '{name}'", expected=(200, 201),
                              headers=self._headers(), json=body).json()
        logger.info("Created group %s (%s)", group.get("uuid"), name)
        return group

    def get_or_create_group(self, name: str, description: Optional[str] = None) -> Dict[str, Any]:
        """Get a group by name, or create it if it doesn't exist.

        Parameters:
            name (str): Name of the group to get or create.
            description (Optional[str]): Description of the group if it needs to be created.

        Returns:
            Dict[str, Any]: The found or created group object.
        """
        return self.find_group_by_name(name) or self.create_group(name, description)

    def patch_group(self, group: Ref, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply a series of JSON Patch operations to a group.

        Parameters:
            group (Ref): UUID or reference of the group to patch.
            operations (List[Dict[str, Any]]): List of JSON Patch operations.

        Returns:
            Dict[str, Any]: The updated group object.
        """
        uuid = self.resolve_group(group)
        response = self._request("PATCH", self._url(GROUPS, f"/{uuid}"), f"Patch group {uuid}",
                                 headers=self._headers(), json=operations)
        self._name_cache.clear()
        return response.json()

    def rename_group(self, group: Ref, new_name: str) -> Dict[str, Any]:
        """Permanent groups and role groups (part of a DSO) cannot be renamed (422).

        Parameters:
            group (Ref): UUID or reference of the group to rename.
            new_name (str): New name for the group.

        Returns:
            Dict[str, Any]: The updated group object.
        """
        return self.patch_group(group, [{"op": "replace", "path": "/name", "value": new_name}])

    def update_group_metadata(self, group: Ref, operation: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Update the metadata of a group using JSON Patch operations.

        Parameters:
            group (Ref): UUID or reference of the group to update.
            operation (str): JSON Patch operation (e.g., "add", "replace", "remove").
            metadata (Dict[str, Any]): Metadata to apply with the operation.

        Returns:
            Dict[str, Any]: The updated group object.
        """
        return self.patch_group(group, self._metadata_patch_ops(operation, metadata))

    def delete_group(self, group: Ref) -> bool:
        """Delete a standalone group. Role groups must be removed via delete_role_group()."""
        uuid = self.resolve_group(group)
        self._request("DELETE", self._url(GROUPS, f"/{uuid}"), f"Delete group {uuid}", expected=(204,))
        self._name_cache.clear()
        logger.info("Deleted group %s", uuid)
        return True

    def get_group_object(self, group: Ref) -> Optional[Dict[str, Any]]:
        """The community/collection a role group belongs to, or None for standalone groups.

        Parameters:
            group (Ref): UUID or reference of the group.

        Returns:
            Optional[Dict[str, Any]]: The linked community/collection object, or None.
        """
        uuid = self.resolve_group(group)
        return self._get_optional(self._url(GROUPS, f"/{uuid}/object"), f"Get object of group {uuid}")

    # ------------------------------------------------------------------
    # Members: subgroups
    # ------------------------------------------------------------------

    def get_subgroups(self, group: Ref, embed_object: bool = True, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Direct subgroups (with their linked community/collection embedded by default).

        Parameters:
            group (Ref): UUID or reference of the parent group.
            embed_object (bool): Whether to embed the full object of the subgroups.
            all_languages (Optional[bool]): Whether to include all languages.
            projection (ProjectionArg): Projection argument for the request.
            embed (EmbedArg): Embed argument for the request.

        Returns:
            List[Dict[str, Any]]: A list of direct subgroups.
        """
        uuid = self.resolve_group(group)
        params = [("embed", "object")] if embed_object else None
        # The REST contract documents "_embedded.groups"; some versions use "subgroups"
        url = self._url(GROUPS, f"/{uuid}/subgroups")
        return list(self._iter_embedded(url, ("subgroups", "groups"), f"Get subgroups of {uuid}", params,
                                        all_languages=all_languages, projection=projection, embed=embed))

    def add_subgroup(self, group: Ref, subgroup: Ref) -> bool:
        """
        Add a subgroup to a parent group.

        Parameters:
            group (Ref): UUID or reference of the parent group.
            subgroup (Ref): UUID or reference of the subgroup to add.

        Returns:
            bool: True if the operation was successful.

        Raises:
            HTTPError: If the subgroup doesn't exist or the link would create a cycle (422).
        """
        parent, child = self.resolve_group(group), self.resolve_group(subgroup)
        self._request("POST", self._url(GROUPS, f"/{parent}/subgroups"), f"Add subgroup {child} to {parent}",
                      expected=(200, 204), headers=self._headers("text/uri-list"),
                      data=self._uri_list(GROUPS, f"/{child}"))
        logger.info("Added subgroup %s to group %s", child, parent)
        return True

    def remove_subgroup(self, group: Ref, subgroup: Ref) -> bool:
        """No-op (still 204) if it wasn't a subgroup.

        Parameters:
            group (Ref): UUID or reference of the parent group.
            subgroup (Ref): UUID or reference of the subgroup to remove.

        Returns:
            bool: True if the operation was successful.
        """
        parent, child = self.resolve_group(group), self.resolve_group(subgroup)
        self._request("DELETE", self._url(GROUPS, f"/{parent}/subgroups/{child}"),
                      f"Remove subgroup {child} from {parent}", expected=(204,))
        logger.info("Removed subgroup %s from group %s", child, parent)
        return True

    # ------------------------------------------------------------------
    # Members: epersons
    # ------------------------------------------------------------------

    def get_group_epersons(self, group: Ref, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Direct eperson members.

        Parameters:
            group (Ref): UUID or reference of the group.
            all_languages (Optional[bool]): Whether to include all languages.
            projection (ProjectionArg): Projection argument for the request.
            embed (EmbedArg): Embed argument for the request.

        Returns:
            List[Dict[str, Any]]: A list of direct eperson members.
        """
        uuid = self.resolve_group(group)
        return list(self._iter_embedded(self._url(GROUPS, f"/{uuid}/epersons"), "epersons",
                                        f"Get epersons of group {uuid}", all_languages=all_languages, projection=projection, embed=embed))

    def add_eperson(self, group: Ref, eperson: Ref) -> bool:
        """Add an eperson to a group.

        Parameters:
            group (Ref): UUID or reference of the group.
            eperson (Ref): UUID or reference of the eperson to add.

        Returns:
            bool: True if the operation was successful.
        """
        group_uuid, person_uuid = self.resolve_group(group), self.epersons.resolve_eperson(eperson)
        self._request("POST", self._url(GROUPS, f"/{group_uuid}/epersons"),
                      f"Add eperson {person_uuid} to {group_uuid}", expected=(200, 204),
                      headers=self._headers("text/uri-list"), data=self._uri_list(EPERSONS, f"/{person_uuid}"))
        logger.info("Added eperson %s to group %s", person_uuid, group_uuid)
        return True

    def remove_eperson(self, group: Ref, eperson: Ref) -> bool:
        """Remove an eperson from a group.

        Parameters:
            group (Ref): UUID or reference of the group.
            eperson (Ref): UUID or reference of the eperson to remove.

        Returns:
            bool: True if the operation was successful.
        """
        group_uuid, person_uuid = self.resolve_group(group), self.epersons.resolve_eperson(eperson)
        self._request("DELETE", self._url(GROUPS, f"/{group_uuid}/epersons/{person_uuid}"),
                      f"Remove eperson {person_uuid} from {group_uuid}", expected=(204,))
        logger.info("Removed eperson %s from group %s", person_uuid, group_uuid)
        return True

    # ------------------------------------------------------------------
    # Members: bulk
    # ------------------------------------------------------------------

    def add_members(self, group: Ref, epersons: Iterable[Ref] = (), subgroups: Iterable[Ref] = ()) -> Dict[str, List[str]]:
        """Add epersons and subgroups, skipping those that are already direct members.

        Parameters:
            group (Ref): UUID or reference of the group.
            epersons (Iterable[Ref], optional): Epersons to add. Defaults to ().
            subgroups (Iterable[Ref], optional): Subgroups to add. Defaults to ().

        Returns:
            Dict[str, List[str]]: A dictionary with keys "added_epersons" and "added_subgroups" listing the added members.
        """
        uuid = self.resolve_group(group)
        result = {"added_epersons": [], "added_subgroups": []}
        epersons, subgroups = list(epersons), list(subgroups)
        if epersons:
            current = {e["uuid"] for e in self.get_group_epersons(uuid)}
            for ref in epersons:
                person = self.epersons.resolve_eperson(ref)
                if person not in current:
                    self.add_eperson(uuid, person)
                    current.add(person)
                    result["added_epersons"].append(person)
        if subgroups:
            current = {g["uuid"] for g in self.get_subgroups(uuid, embed_object=False)}
            for ref in subgroups:
                child = self.resolve_group(ref)
                if child not in current:
                    self.add_subgroup(uuid, child)
                    current.add(child)
                    result["added_subgroups"].append(child)
        return result

    def sync_members(self, group: Ref, epersons: Optional[Iterable[Ref]] = None,
                     subgroups: Optional[Iterable[Ref]] = None, dry_run: bool = False) -> Dict[str, List[str]]:
        """
        Make the direct members exactly the given lists. None leaves that kind
        of member untouched; an empty list removes all of them.

        Parameters:
            group (Ref): UUID or reference of the group.
            epersons (Optional[Iterable[Ref]]): Desired list of eperson members. None leaves unchanged.
            subgroups (Optional[Iterable[Ref]]): Desired list of subgroup members. None leaves unchanged.
            dry_run (bool): If True, only simulate the changes without applying them.

        Returns:
            Dict[str, List[str]]: A dictionary with keys "added_epersons", "removed_epersons", "added_subgroups", and "removed_subgroups" listing the changes made.
        """
        uuid = self.resolve_group(group)
        result = {"added_epersons": [], "removed_epersons": [], "added_subgroups": [], "removed_subgroups": []}
        if epersons is not None:
            wanted = {self.epersons.resolve_eperson(e) for e in epersons}
            current = {e["uuid"] for e in self.get_group_epersons(uuid)}
            result["added_epersons"] = sorted(wanted - current)
            result["removed_epersons"] = sorted(current - wanted)
        if subgroups is not None:
            wanted = {self.resolve_group(g) for g in subgroups}
            current = {g["uuid"] for g in self.get_subgroups(uuid, embed_object=False)}
            result["added_subgroups"] = sorted(wanted - current)
            result["removed_subgroups"] = sorted(current - wanted)
        if dry_run:
            logger.info("[dry-run] sync_members %s: %s", uuid, result)
            return result
        for e in result["added_epersons"]:
            self.add_eperson(uuid, e)
        for e in result["removed_epersons"]:
            self.remove_eperson(uuid, e)
        for g in result["added_subgroups"]:
            self.add_subgroup(uuid, g)
        for g in result["removed_subgroups"]:
            self.remove_subgroup(uuid, g)
        return result

    # ------------------------------------------------------------------
    # Role groups of communities and collections
    # ------------------------------------------------------------------

    def _role_url(self, dso_type: str, dso_uuid: str, role: str) -> str:
        """Construct the URL for a role group of a community or collection.

        Parameters:
            dso_type (str): Either "community" or "collection".
            dso_uuid (str): UUID of the community or collection.
            role (str): Role name.

        Returns:
            str: The URL for the role group.

        Raises:
            ValueError: If the dso_type or role is invalid.
        """
        if dso_type not in _DSO_PATHS:
            raise ValueError(f"dso_type must be 'community' or 'collection', not {dso_type!r}")
        allowed = COMMUNITY_ROLES if dso_type == "community" else COLLECTION_ROLES
        if role not in allowed and not (dso_type == "collection" and role.startswith(WORKFLOW_GROUP_PREFIX)
                                        and len(role) > len(WORKFLOW_GROUP_PREFIX)):
            raise ValueError(f"Unknown {dso_type} role {role!r}; expected one of {allowed}"
                             + (" or 'workflowGroups/<role>'" if dso_type == "collection" else ""))
        return self._url(_DSO_PATHS[dso_type], f"/{dso_uuid}/{role}")

    def get_role_group(self, dso_type: str, dso_uuid: str, role: str) -> Optional[Dict[str, Any]]:
        """
        The role group, or None if it hasn't been created (204).

        For itemReadGroup / bitstreamReadGroup DSpace returns the group of
        the collection's DEFAULT_*_READ policy - on an unrestricted
        collection that is the Anonymous group itself. That is reported as
        None ("no read group yet"), so nobody adds members to Anonymous by
        accident (which would affect every object readable by Anonymous).

        Parameters:
            dso_type (str): Either "community" or "collection".
            dso_uuid (str): UUID of the community or collection.
            role (str): Role name.

        Returns:
            Optional[Dict[str, Any]]: The role group if it exists, otherwise None.
        """
        group = self._get_optional(self._role_url(dso_type, dso_uuid, role), f"Get {role} of {dso_type} {dso_uuid}")
        if group and role in (ITEM_READ_GROUP, BITSTREAM_READ_GROUP) and \
                str(group.get("name", "")).lower() == "anonymous":
            return None
        return group

    def create_role_group(self, dso_type: str, dso_uuid: str, role: str, description: Optional[str] = None,
                          metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Create a role group; DSpace names it itself (e.g. COLLECTION_<uuid>_SUBMIT).
        For itemReadGroup / bitstreamReadGroup this replaces the Anonymous
        DEFAULT_*_READ policy of the collection with the new group.

        Parameters:
            dso_type (str): Either "community" or "collection".
            dso_uuid (str): UUID of the community or collection.
            role (str): Role name.
            description (Optional[str]): Description for the role group.
            metadata (Optional[Dict[str, Any]]): Additional metadata for the role group.

        Returns:
            Dict[str, Any]: The created role group.
        """
        md = dict(metadata or {})
        if description and "dc.description" not in md:
            md["dc.description"] = description
        url = self._role_url(dso_type, dso_uuid, role)
        group = self._request("POST", url, f"Create {role} of {dso_type} {dso_uuid}", expected=(200, 201),
                              headers=self._headers(), json={"metadata": self._format_metadata(md)}).json()
        logger.info("Created %s %s for %s %s", role, group.get("uuid"), dso_type, dso_uuid)
        return group

    def get_or_create_role_group(self, dso_type: str, dso_uuid: str, role: str,
                                 description: Optional[str] = None) -> Dict[str, Any]:
        """Get the role group if it exists, otherwise create it.

        Parameters:
            dso_type (str): Either "community" or "collection".
            dso_uuid (str): UUID of the community or collection.
            role (str): Role name.
            description (Optional[str], optional): Description for the role group. Defaults to None.

        Returns:
            Dict[str, Any]: The role group.
        """
        return (self.get_role_group(dso_type, dso_uuid, role)
                or self.create_role_group(dso_type, dso_uuid, role, description))

    def delete_role_group(self, dso_type: str, dso_uuid: str, role: str) -> bool:
        """Delete the role group.

        Parameters:
            dso_type (str): Either "community" or "collection".
            dso_uuid (str): UUID of the community or collection.
            role (str): Role name.

        Returns:
            bool: True if the role group was deleted successfully.
        """
        self._request("DELETE", self._role_url(dso_type, dso_uuid, role), f"Delete {role} of {dso_type} {dso_uuid}",
                      expected=(204,))
        logger.info("Deleted %s of %s %s", role, dso_type, dso_uuid)
        return True

    def ensure_role_members(self, dso_type: str, dso_uuid: str, role: str, epersons: Iterable[Ref] = (),
                            subgroups: Iterable[Ref] = (), description: Optional[str] = None) -> Dict[str, Any]:
        """Create the role group if needed and add the members. Returns {"group", "added_*"}.

        Parameters:
            dso_type (str): Either "community" or "collection".
            dso_uuid (str): UUID of the community or collection.
            role (str): Role name.
            epersons (Iterable[Ref], optional): Epersons to add. Defaults to ().
            subgroups (Iterable[Ref], optional): Subgroups to add. Defaults to ().
            description (Optional[str], optional): Description for the role group. Defaults to None.

        Returns:
            Dict[str, Any]: A dictionary containing the role group and the added members.
        """
        group = self.get_or_create_role_group(dso_type, dso_uuid, role, description)
        return {"group": group, **self.add_members(group["uuid"], epersons, subgroups)}
