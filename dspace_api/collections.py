# -*- coding: UTF-8 -*-
"""
Collection management for DSpace API

    GET|POST /core/collections/{uuid}/logo, DELETE via the logo bitstream
    GET      /core/collections/{uuid}/parentCommunity
    GET      /core/collections/search/findAdminAuthorized|findEditAuthorized
    GET      /core/collections/search/findSubmitAuthorized[ByEntityType|ByCommunity|ByCommunityAndEntityType]
    GET      /core/collections/{uuid}/license          (custom license or the site default)
    GET|POST /core/collections/{uuid}/itemtemplate     PATCH|DELETE /core/itemtemplates/{uuid}
    GET      /core/collections/{uuid}/defaultAccessConditions
    GET      /core/collections/{uuid}/mappedItems      POST|DELETE /core/items/{item}/mappedCollections[/{uuid}]
    GET|PUT  /core/collections/{uuid}/harvester        GET /config/harvestermetadata

Logo, parent community, patch_metadata and the *Authorized searches are
shared with communities (_containers.py).
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Union
from ._base import BaseManager
from ._containers import ContainerMixin
from .metadata import MetadataPatch
from .projection import EmbedArg, ProjectionArg
from .communities import build_dso_metadata
from .config import Endpoints
from .projection import view_params
from .exceptions import NotFoundError, handle_response_error

# logger for this module
logger = logging.getLogger(__name__)

# Collection role groups (see GroupManager)
ADMIN = "adminGroup"
SUBMITTERS = "submittersGroup"
ITEM_READ = "itemReadGroup"
BITSTREAM_READ = "bitstreamReadGroup"

Ref = Union[str, Dict[str, Any]]


def build_collection_metadata(title: str, description: Optional[str] = None, abstract: Optional[str] = None,
                              rights: Optional[str] = None, table_of_contents: Optional[str] = None,
                              license: Optional[str] = None, entity_type: Optional[str] = None,
                              submission_definition: Optional[str] = None,
                              submission_definition_correction: Optional[str] = None,
                              workspace_shared: Optional[bool] = None,
                              language: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Metadata for a (DSpace-CRIS) collection: the community fields plus
    dc.rights.license, dspace.entity.type, cris.submission.definition,
    cris.submission.definition-correction and cris.workspace.shared.

    Parameters:
        title (str): The title of the collection.
        description (Optional[str]): The description of the collection.
        abstract (Optional[str]): The abstract of the collection.
        rights (Optional[str]): The rights information of the collection.
        table_of_contents (Optional[str]): The table of contents of the collection.
        license (Optional[str]): The license of the collection.
        entity_type (Optional[str]): The entity type of the collection.
        submission_definition (Optional[str]): The submission definition of the collection.
        submission_definition_correction (Optional[str]): The submission definition correction of the collection.
        workspace_shared (Optional[bool]): Whether the workspace is shared.
        language (Optional[str]): The language of the collection.

    Returns:
        Dict[str, List[Dict[str, Any]]]: The structured metadata for the collection.
    """
    return build_dso_metadata(title, description, abstract, rights, table_of_contents, language, **{
        "dc.rights.license": license,
        "dspace.entity.type": entity_type,
        "cris.submission.definition": submission_definition,
        "cris.submission.definition-correction": submission_definition_correction,
        "cris.workspace.shared": workspace_shared,
    })


class CollectionManager(ContainerMixin, BaseManager):
    """Manages DSpace collections, their role groups, submission policies and settings"""

    # The API endpoint and key for collections within DSpace
    _CONTAINER_PATH = Endpoints.COLLECTIONS
    _CONTAINER_KEY = "collections"

    def __init__(self, auth):
        """Initialize the CollectionManager.
        
        Parameters:
            auth: The authentication object used to interact with the DSpace API.   

        Returns:
            None
        """
        logger.info("Initializing CollectionManager")
        super().__init__(auth)
        self._group_manager = None
        self._permission_manager = None

    @property
    def groups(self):
        """Get the group manager for collections.

        Returns:
            GroupManager: The group manager instance.
        """
        if self._group_manager is None:
            from .groups import GroupManager
            self._group_manager = GroupManager(self.auth)
        return self._group_manager

    @property
    def discovery(self):
        """Get the discovery manager for collections.

        Returns:
            DiscoveryManager: The discovery manager instance.
        """
        if getattr(self, "_discovery_manager", None) is None:
            from .discovery import DiscoveryManager
            self._discovery_manager = DiscoveryManager(self.auth)
        return self._discovery_manager

    @property
    def permissions(self):
        """Get the permission manager for collections.

        Returns:
            PermissionManager: The permission manager instance.
        """
        if self._permission_manager is None:
            from .permissions import PermissionManager
            self._permission_manager = PermissionManager(self.auth)
        return self._permission_manager

    def get_collections(self, page=0, size=20, sort=None, all_languages=None, projection=None, embed=None):
        """
        Get a list of collections

        Parameters:
            page (int): Page number (0-based)
            size (int): Number of collections per page
            sort (Optional[str]): Sort criteria
            all_languages (Optional[bool]): Whether to include all languages
            projection (Optional[str]): Projection parameter for the API
            embed (Optional[str]): Embed parameter for the API

        Returns:
            dict: API response containing collections
        """
        logger.info("Getting collections (page=%d, size=%d)", page, size)

        url = f"{self.config.dspace_url}{Endpoints.COLLECTIONS}"
        params = {"page": page, "size": size}
        if sort:
            params["sort"] = sort
        params = view_params(params, self.config, all_languages, projection, embed)

        try:
            response = self.session.get(
                url,
                headers=self.config.headers,
                params=params,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Successfully retrieved collections")
                return response.json()
            else:
                handle_response_error(response, "Get collections")

        except Exception as e:
            logger.error("Failed to get collections: %s", e)
            raise

    def get_collection(self, collection_uuid, all_languages=None, projection=None, embed=None):
        """
        Get a specific collection by UUID

        Parameters:
            collection_uuid (str): UUID of the collection

        Returns:
            dict: Collection data
        """
        logger.info("Getting collection %s", collection_uuid)

        url = f"{self.config.dspace_url}{Endpoints.COLLECTIONS}/{collection_uuid}"
        params = view_params(None, self.config, all_languages, projection, embed)

        try:
            response = self.session.get(
                url,
                headers=self.config.headers,
                params=params,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Successfully retrieved collection %s", collection_uuid)
                return response.json()
            handle_response_error(response, "Get collection %s" % collection_uuid)

        except Exception as e:
            logger.error("Failed to get collection %s: %s", collection_uuid, e)
            raise

    def get_collection_items(self, collection_uuid, page=0, size=20, sort=None, all_languages=None, projection=None,
                             embed=None, query=None, filters=None):
        """
        One page of the items in a collection.

        DSpace 7+ has no /core/collections/{uuid}/items endpoint; like the
        Angular UI, this uses a discovery search scoped to the collection
        (dsoType=item). The result keeps the familiar shape:

            {"_embedded": {"items": [item, ...]}, "page": {"number", "size", "totalElements", "totalPages"}}

        Parameters:
            collection_uuid: UUID of the collection
            page (int): 0-based page number
            size (int): Page size
            sort: discovery sort option, e.g. "dc.title,ASC", "dc.date.accessioned,DESC"
                  (see DiscoveryManager.get_sort_options())
            query (Optional[str]): Optional discovery query
            filters (Optional[dict]): Optional discovery filters (see DiscoveryManager.search)

        Returns:
            dict: A dictionary containing the items and pagination information.

        Notes:
            - Items mapped into the collection are included (discovery scope covers them).
            - Discovery shows what the current user may see: withdrawn and
              private (non-discoverable) items only appear for administrators.
            - Newly created items appear once Solr has indexed them.
        """
        logger.info("Getting items for collection %s (page %s)", collection_uuid, page)
        response = self.discovery.search(query=query, dso_type="item", scope=collection_uuid, filters=filters,
                                         sort=sort, page=page, size=size, all_languages=all_languages,
                                         projection=projection, embed=embed)
        objects, info = self.discovery.search_result(response)
        items = [(o.get("_embedded") or {}).get("indexableObject") for o in objects]
        return {"_embedded": {"items": [i for i in items if i]},
                "page": {"number": info.get("number", page), "size": info.get("size", size),
                         "totalElements": info.get("totalElements", 0),
                         "totalPages": info.get("totalPages", 0)}}

    def iter_collection_items(self, collection_uuid, page_size=100, max_results=None, sort=None, query=None,
                              filters=None, all_languages=None, projection=None, embed=None):
        """Every item in a collection, across all pages (a generator). See get_collection_items().

        Parameters:
            collection_uuid (str): UUID of the collection
            page_size (int): Number of items per page
            max_results (Optional[int]): Maximum number of items to return
            sort (Optional[str]): Sort criteria
            query (Optional[str]): Optional discovery query
            filters (Optional[dict]): Optional discovery filters
            all_languages (Optional[bool]): Whether to include all languages
            projection (Optional[str]): Projection parameter for the API
            embed (Optional[str]): Embed parameter for the API

        Yields:
            dict: Items in the collection
        """
        return self.discovery.iter_search(query=query, dso_type="item", scope=collection_uuid, filters=filters,
                                          sort=sort, page_size=page_size, max_results=max_results,
                                          all_languages=all_languages, projection=projection, embed=embed)

    def count_collection_items(self, collection_uuid, query=None, filters=None):
        """Number of items in a collection visible to the current user (one request).
        
        Parameters:
            collection_uuid (str): UUID of the collection
            query (Optional[str]): Optional discovery query
            filters (Optional[dict]): Optional discovery filters

        Returns:
            int: Number of items in the collection
        """
        return self.discovery.count(query=query, dso_type="item", scope=collection_uuid, filters=filters)

    def create_collection(self, parent_community_uuid, name, metadata=None):
        """
        Create a new collection.

        This uses POST /core/collections?parent={communityUUID}

        Parameters:
            parent_community_uuid (str): UUID of the parent community
            name (str): Friendly name for the collection
            metadata (Optional[dict]): Optional metadata dict. If 'dc.title' is missing, it will be set from `name`.

        Returns:
            dict: Created collection data
        """
        logger.info("Creating collection '%s' in community %s", name, parent_community_uuid)

        # Ensure metadata exists and contains dc.title from name if not supplied
        metadata = dict(metadata or {})
        if "dc.title" not in metadata:
            metadata["dc.title"] = [{"value": name}]
        else:
            # normalize metadata field variants via formatter
            pass

        collection_data = {
            # Some DSpace installations accept/require this top-level name as well:
            "name": name,
            "metadata": self._format_metadata(metadata),
            "type": "collection",
        }

        url = f"{self.config.dspace_url}{Endpoints.COLLECTIONS}?parent={parent_community_uuid}"

        try:
            response = self.session.post(
                url,
                headers=self.config.headers,
                json=collection_data,
                timeout=self.config.timeout
            )
            if response.status_code in (200, 201):
                logger.info("Collection created successfully")
                return response.json()
            handle_response_error(response, "Create collection")
        except Exception as e:
            logger.error("Failed to create collection: %s", e)
            raise

    def update_collection(self, collection_uuid, name=None, metadata=None):
        """
        Update collection metadata.

        Parameters:
            collection_uuid (str): UUID of the collection
            name (Optional[str]): New name for the collection (maps to metadata 'dc.title')
            metadata (Optional[dict]): Dict of metadata fields to replace. NOTE: 'dspace.entity.type' will be ignored.

        Returns:
            dict: Updated collection data
        """
        logger.info("Updating collection %s", collection_uuid)

        operations = []

        # If a name is provided, update the dc.title field instead of /name
        if name:
            operations.append({
                "op": "replace",
                "path": "/metadata/dc.title",
                "value": {"value": name}
            })

        if metadata:
            # NEVER allow changing dspace.entity.type via this method
            safe_metadata = {
                k: v for k, v in metadata.items()
                if k != "dspace.entity.type"
            }

            for field, values in safe_metadata.items():
                # If caller passed a simple string or boolean, wrap once
                if isinstance(values, (str, bool, int, float)):
                    value_obj = {"value": values}
                    operations.append({
                        "op": "replace",
                        "path": f"/metadata/{field}",
                        "value": value_obj
                    })
                elif isinstance(values, list):
                    # If they passed a list of primitives/objects, normalize to list of {"value": ...}
                    normalized = []
                    for v in values:
                        if isinstance(v, dict) and "value" in v:
                            normalized.append(v)
                        else:
                            normalized.append({"value": v})
                    operations.append({
                        "op": "replace",
                        "path": f"/metadata/{field}",
                        "value": normalized if len(normalized) > 1 else normalized[0]
                    })
                elif isinstance(values, dict):
                    # Assume already a MetadataValue-like object
                    operations.append({
                        "op": "replace",
                        "path": f"/metadata/{field}",
                        "value": values
                    })
                else:
                    operations.append({
                        "op": "replace",
                        "path": f"/metadata/{field}",
                        "value": {"value": str(values)}
                    })

        url = f"{self.config.dspace_url}{Endpoints.COLLECTIONS}/{collection_uuid}"

        try:
            headers = dict(self.config.headers)
            headers["Content-Type"] = "application/json-patch+json"

            response = self.session.patch(
                url,
                headers=headers,
                json=operations,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Collection %s updated successfully", collection_uuid)
                return response.json()

            if response.status_code == 204:
                logger.info("Collection %s updated successfully (204). Fetching resource...", collection_uuid)
                return self.get_collection(collection_uuid)

            handle_response_error(response, f"Update collection {collection_uuid}")

        except Exception as e:
            logger.error("Failed to update collection %s: %s", collection_uuid, e)
            raise

    def delete_collection(self, collection_uuid):
        """
        Delete a collection

        Parameters:
            collection_uuid (str): UUID of the collection to delete

        Returns:
            bool: True if deletion was successful
        """
        logger.info("Deleting collection %s", collection_uuid)

        url = f"{self.config.dspace_url}{Endpoints.COLLECTIONS}/{collection_uuid}"

        try:
            response = self.session.delete(
                url,
                headers=self.config.headers,
                timeout=self.config.timeout
            )

            if response.status_code == 204:
                logger.info("Collection %s deleted successfully", collection_uuid)
                return True
            handle_response_error(response, f"Delete collection {collection_uuid}")

        except Exception as e:
            logger.error("Failed to delete collection %s: %s", collection_uuid, e)
            raise

    def _format_metadata(self, metadata_dict):
        """
        Format metadata dictionary for DSpace API

        Parameters:
            metadata_dict (dict): Dictionary of metadata fields

        Returns:
            dict: Formatted metadata
        """
        formatted = {}

        for field, values in metadata_dict.items():
            formatted[field] = self._format_metadata_field(values)

        return formatted

    def _format_metadata_field(self, values):
        """
        Format metadata field values for DSpace API

        Parameters:
            values (Union[str, List[str], List[dict]]): String, list of strings, or list of dicts

        Returns:
            list: Formatted metadata values
        """
        if isinstance(values, str):
            return [{"value": values}]
        elif isinstance(values, list):
            formatted = []
            for value in values:
                if isinstance(value, str):
                    formatted.append({"value": value})
                elif isinstance(value, dict):
                    formatted.append(value)
                else:
                    formatted.append({"value": str(value)})
            return formatted
        else:
            return [{"value": str(values)}]

    # ------------------------------------------------------------------
    # Finding collections
    # ------------------------------------------------------------------

    def search_editable_collections(self, query: str = "", configuration: str = "editCollection",
                                    max_results: Optional[int] = 20,
                                    all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """
        Collections the current user can edit (discovery configuration).
        If your discovery.xml names the configuration differently, pass it.

        Parameters:
            query (str): Discovery query string
            configuration (str): Discovery configuration name
            max_results (Optional[int]): Maximum number of results to return
            all_languages (Optional[bool]): Whether to include all languages
            projection (ProjectionArg): Projection argument for the API
            embed (EmbedArg): Embed argument for the API

        Returns:
            List[Dict[str, Any]]: List of collections the current user can edit
        """
        return self._discover(query, dso_type="COLLECTION", configuration=configuration, sort="dc.title,ASC",
                              max_results=max_results, all_languages=all_languages, projection=projection, embed=embed)

    def find_submit_authorized(self, query: Optional[str] = None, entity_type: Optional[str] = None,
                               max_results: Optional[int] = None,
                               all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None,
                               community_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Collections the current user may submit to, optionally of one entity type and/or below one community.
        
        Parameters:
            query (Optional[str]): Optional discovery query
            entity_type (Optional[str]): Optional entity type filter
            max_results (Optional[int]): Maximum number of results to return
            all_languages (Optional[bool]): Whether to include all languages
            projection (ProjectionArg): Projection argument for the API
            embed (EmbedArg): Embed argument for the API
            community_uuid (Optional[str]): UUID of the community to filter by

        Returns:
            List[Dict[str, Any]]: List of collections the current user may submit to
        """
        method = "findSubmitAuthorized" + ("ByCommunity" if community_uuid else "") + \
            (("AndEntityType" if community_uuid else "ByEntityType") if entity_type else "")
        url = self._url(Endpoints.COLLECTIONS, f"/search/{method}")
        params = [("uuid", community_uuid)] if community_uuid else []
        if entity_type:
            params.append(("entityType", entity_type))
        if query:
            params.append(("query", query))
        return list(self._iter_embedded(url, "collections", "Find submit-authorized collections", params,
                                        max_results=max_results, all_languages=all_languages, projection=projection, embed=embed))

    # ------------------------------------------------------------------
    # Role groups: admin, submitters, default item/bitstream read, workflow
    # ------------------------------------------------------------------

    def get_role_group(self, collection_uuid: str, role: str) -> Optional[Dict[str, Any]]:
        """Get the role group for a collection.

        Parameters:
            collection_uuid (str): UUID of the collection
            role (str): Role name (adminGroup, submittersGroup, itemReadGroup, bitstreamReadGroup or workflowGroups/<name>)

        Returns:
            Optional[Dict[str, Any]]: Role group information if it exists, otherwise None.
        """
        return self.groups.get_role_group("collection", collection_uuid, role)

    def get_or_create_role_group(self, collection_uuid: str, role: str,
                                 description: Optional[str] = None) -> Dict[str, Any]:
        """Get or create the role group for a collection.

        Parameters:
            collection_uuid (str): UUID of the collection
            role (str): Role name (adminGroup, submittersGroup, itemReadGroup, bitstreamReadGroup or workflowGroups/<name>)
            description (Optional[str]): Description for the role group

        Returns:
            Dict[str, Any]: Role group information.
        """
        return self.groups.get_or_create_role_group("collection", collection_uuid, role, description)

    def delete_role_group(self, collection_uuid: str, role: str) -> bool:
        """Delete the role group for a collection.

        Parameters:
            collection_uuid (str): UUID of the collection
            role (str): Role name

        Returns:
            bool: True if the role group was deleted, False otherwise.
        """
        return self.groups.delete_role_group("collection", collection_uuid, role)

    def add_role_members(self, collection_uuid: str, role: str, epersons: Iterable[Ref] = (),
                         subgroups: Iterable[Ref] = (), description: Optional[str] = None) -> Dict[str, Any]:
        """Create the role group if needed and add epersons and/or groups to it.

        Parameters:
            collection_uuid (str): UUID of the collection
            role (str): Role name
            epersons (Iterable[Ref]): Epersons to add to the role group
            subgroups (Iterable[Ref]): Subgroups to add to the role group
            description (Optional[str]): Description for the role group

        Returns:
            Dict[str, Any]: Role group information.
        """
        return self.groups.ensure_role_members("collection", collection_uuid, role, epersons, subgroups, description)

    def get_role_members(self, collection_uuid: str, role: str) -> Dict[str, List[Dict[str, Any]]]:
        """Get the members of a role group.

        Parameters:
            collection_uuid (str): UUID of the collection
            role (str): Role name

        Returns:
            Dict[str, List[Dict[str, Any]]]: {"epersons": [...], "subgroups": [...]}; both empty if the role group doesn't exist.
        """
        group = self.get_role_group(collection_uuid, role)
        if not group:
            return {"epersons": [], "subgroups": []}
        return {"epersons": self.groups.get_group_epersons(group["uuid"]),
                "subgroups": self.groups.get_subgroups(group["uuid"])}

    def add_admins(self, collection_uuid: str, epersons: Iterable[Ref] = (), subgroups: Iterable[Ref] = ()):
        """Add epersons and/or subgroups to the admin role group.

        Parameters:
            collection_uuid (str): UUID of the collection
            epersons (Iterable[Ref]): Epersons to add to the role group
            subgroups (Iterable[Ref]): Subgroups to add to the role group

        Returns:
            Dict[str, Any]: Role group information.
        """
        return self.add_role_members(collection_uuid, ADMIN, epersons, subgroups)

    def add_submitters(self, collection_uuid: str, epersons: Iterable[Ref] = (), subgroups: Iterable[Ref] = ()):
        """Add epersons and/or subgroups to the submitters role group.

        Parameters:
            collection_uuid (str): UUID of the collection
            epersons (Iterable[Ref]): Epersons to add to the role group
            subgroups (Iterable[Ref]): Subgroups to add to the role group

        Returns:
            Dict[str, Any]: Role group information.
        """
        return self.add_role_members(collection_uuid, SUBMITTERS, epersons, subgroups)

    def restrict_item_read(self, collection_uuid: str, epersons: Iterable[Ref] = (), subgroups: Iterable[Ref] = (),
                           description: Optional[str] = None) -> Dict[str, Any]:
        """Make items deposited from now on readable only by the given members
        (creates itemReadGroup, which replaces Anonymous as default reader).
        Items already in the collection keep their policies - use
        PermissionManager.remove_group_access_from_item() for those.

        Parameters:
            collection_uuid (str): UUID of the collection
            epersons (Iterable[Ref]): Epersons to add to the role group
            subgroups (Iterable[Ref]): Subgroups to add to the role group
            description (Optional[str]): Description for the role group

        Returns:
            Dict[str, Any]: Role group information.
        """
        return self.add_role_members(collection_uuid, ITEM_READ, epersons, subgroups, description)

    def restrict_bitstream_read(self, collection_uuid: str, epersons: Iterable[Ref] = (),
                                subgroups: Iterable[Ref] = (), description: Optional[str] = None) -> Dict[str, Any]:
        """Same as restrict_item_read() for the files (bitstreams) of new items.

        Parameters:
            collection_uuid (str): UUID of the collection
            epersons (Iterable[Ref]): Epersons to add to the role group
            subgroups (Iterable[Ref]): Subgroups to add to the role group
            description (Optional[str]): Description for the role group

        Returns:
            Dict[str, Any]: Role group information.
        """
        return self.add_role_members(collection_uuid, BITSTREAM_READ, epersons, subgroups, description)

    def is_read_restricted(self, collection_uuid: str) -> Dict[str, bool]:
        """Check if new items / new bitstreams are publicly readable, based on the
        collection's DEFAULT_ITEM_READ / DEFAULT_BITSTREAM_READ policies.

        Parameters:
            collection_uuid (str): UUID of the collection

        Returns:
            Dict[str, bool]: Dictionary with keys "items" and "bitstreams" indicating readability.
        """
        from .permissions import ANONYMOUS, Actions
        policies = self.permissions.get_resource_policies(collection_uuid)
        result = {}
        for key, action in (("items", Actions.DEFAULT_ITEM_READ), ("bitstreams", Actions.DEFAULT_BITSTREAM_READ)):
            result[key] = not self.permissions.filter_policies(policies, group=ANONYMOUS, actions=[action])
        return result

    # ------------------------------------------------------------------
    # Submission policies (TYPE_SUBMISSION resource policies on the collection)
    # ------------------------------------------------------------------

    def get_submission_policies(self, collection_uuid: str) -> List[Dict[str, Any]]:
        """Get all TYPE_SUBMISSION policies for the collection.

        Parameters:
            collection_uuid (str): UUID of the collection

        Returns:
            List[Dict[str, Any]]: List of submission policies.
        """
        from .permissions import PolicyTypes
        policies = self.permissions.get_resource_policies(collection_uuid)
        return self.permissions.filter_policies(policies, policy_types=[PolicyTypes.SUBMISSION])

    def add_submission_policy(self, collection_uuid: str, group: Ref, action: str = "ADD",
                              name: Optional[str] = None, start_date=None, end_date=None) -> Dict[str, Any]:
        """Grant a group an action on the collection as a TYPE_SUBMISSION policy (admin only).

        Parameters:
            collection_uuid (str): UUID of the collection
            group (Ref): Group to grant the action to
            action (str): Action to grant (default "ADD")
            name (Optional[str]): Name of the policy
            start_date (Optional): Start date of the policy
            end_date (Optional): End date of the policy

        Returns:
            Dict[str, Any]: Policy information.
        """
        from .permissions import PolicyTypes
        group_uuid = self.groups.resolve_group(group)
        return self.permissions.create_policy(collection_uuid, action=action, group=group_uuid,
                                              policy_type=PolicyTypes.SUBMISSION, name=name,
                                              start_date=start_date, end_date=end_date)

    def delete_submission_policy(self, policy_id: int) -> bool:
        """Delete a TYPE_SUBMISSION policy by its ID.

        Parameters:
            policy_id (int): ID of the policy to delete

        Returns:
            bool: True if the policy was successfully deleted, False otherwise.
        """
        return self.permissions.delete_policy(policy_id)

    # ------------------------------------------------------------------
    # License
    # ------------------------------------------------------------------

    def get_license(self, collection_uuid: str) -> Dict[str, Any]:
        """
        Get the license of the collection.

        {"custom": bool, "text": ...} - the collection's license or the site default.

        Parameters:
            collection_uuid (str): UUID of the collection

        Returns:
            Dict[str, Any]: License information.
        """
        return self._get_json(self._url(Endpoints.COLLECTIONS, f"/{collection_uuid}/license"),
                              f"Get license of collection {collection_uuid}")

    def set_license(self, collection_uuid: str, text: Optional[str]) -> Dict[str, Any]:
        """Set a custom deposit license (dc.rights.license); text=None returns to the site default.

        Parameters:
            collection_uuid (str): UUID of the collection
            text (Optional[str]): License text to set, or None to revert to the site default

        Returns:
            Dict[str, Any]: License information.
        """
        patch = MetadataPatch()
        if text is None:
            current = self.get_collection(collection_uuid, all_languages=True) or {}
            if not (current.get("metadata") or {}).get("dc.rights.license"):
                return current
            patch.remove("dc.rights.license")
        else:
            patch.set("dc.rights.license", text)
        return self.patch_metadata(collection_uuid, patch)

    # ------------------------------------------------------------------
    # Item template
    # ------------------------------------------------------------------

    def get_item_template(self, collection_uuid: str, all_languages: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        """
        The template item (metadata copied into new submissions), or None.
        DSpace answers 404 both for "no template" and for an unknown
        collection; the second case still raises NotFoundError.
        
        Parameters:
            collection_uuid (str): UUID of the collection
            all_languages (Optional[bool]): Whether to retrieve metadata in all languages

        Returns:
            Optional[Dict[str, Any]]: Item template metadata, or None if no template exists.
        """
        try:
            return self._get_optional(self._url(Endpoints.COLLECTIONS, f"/{collection_uuid}/itemtemplate"),
                                      f"Get item template of collection {collection_uuid}",
                                      all_languages=all_languages)
        except NotFoundError:
            # raises NotFoundError itself if the collection does not exist
            self._get_json(self._url(Endpoints.COLLECTIONS, f"/{collection_uuid}"),
                           f"Get collection {collection_uuid}", all_languages=False)
            return None

    def create_item_template(self, collection_uuid: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Create an item template for the collection.

        Parameters:
            collection_uuid (str): UUID of the collection
            metadata (Dict[str, Any]): Metadata for the item template

        Returns:
            Dict[str, Any]: Created item template

        Raises:
            ValueError: If the collection already has a template (HTTP 422)
        """
        body = {"metadata": self._format_metadata(metadata), "inArchive": False, "discoverable": False,
                "withdrawn": False, "type": "item"}
        template = self._request("POST", self._url(Endpoints.COLLECTIONS, f"/{collection_uuid}/itemtemplate"),
                                 f"Create item template of collection {collection_uuid}", expected=(200, 201),
                                 headers=self._headers(), json=body).json()
        logger.info("Created item template %s for collection %s", template.get("uuid"), collection_uuid)
        return template

    def update_item_template(self, template_uuid: str, patch: Union[MetadataPatch, Dict[str, Any]]) -> Dict[str, Any]:
        """Update an item template with the given metadata patch.

        Parameters:
            template_uuid (str): UUID of the item template
            patch (Union[MetadataPatch, Dict[str, Any]]): Metadata patch to apply. Can be a MetadataPatch instance or a dictionary {field: values} where each replaces the field.

        Returns:
            Dict[str, Any]: Updated item template

        Raises:
            ValueError: If the patch is empty
        """
        if isinstance(patch, dict):
            built = MetadataPatch()
            for field, values in patch.items():
                built.set(field, values)
            patch = built
        if not len(patch):
            raise ValueError("Empty metadata patch")
        return self._request("PATCH", self._url("/core/itemtemplates", f"/{template_uuid}"),
                             f"Update item template {template_uuid}", headers=self._headers(),
                             json=patch.operations).json()

    def set_item_template(self, collection_uuid: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Create the template, or set these fields on the existing one (other fields stay).

        Parameters:
            collection_uuid (str): UUID of the collection
            metadata (Dict[str, Any]): Metadata to set on the item template

        Returns:
            Dict[str, Any]: Created or updated item template
        """
        template = self.get_item_template(collection_uuid)
        if template is None:
            return self.create_item_template(collection_uuid, metadata)
        return self.update_item_template(template["uuid"], metadata)

    def delete_item_template(self, collection_uuid: str) -> bool:
        """Remove the collection's template; False if it has none.
        
        Parameters:
            collection_uuid (str): UUID of the collection

        Returns:
            bool: True if the template was deleted, False if it had none.
        """
        template = self.get_item_template(collection_uuid)
        if template is None:
            return False
        self._request("DELETE", self._url("/core/itemtemplates", f"/{template['uuid']}"),
                      f"Delete item template {template['uuid']}", expected=(204,))
        return True

    # ------------------------------------------------------------------
    # Default access conditions
    # ------------------------------------------------------------------

    def get_default_access_conditions(self, collection_uuid: str, embed: EmbedArg = "group") -> List[Dict[str, Any]]:
        """
        The DEFAULT_BITSTREAM_READ policies new files inherit (a resource
        policy search; DSpace 7+ has no /defaultAccessConditions endpoint).

        Parameters:
            collection_uuid (str): UUID of the collection
            embed (EmbedArg): Embedding options for the response

        Returns:
            List[Dict[str, Any]]: List of default access condition policies
        """
        from .permissions import Actions
        embeds = [embed] if isinstance(embed, str) else list(embed or [])
        return self.permissions.get_resource_policies(collection_uuid, action=Actions.DEFAULT_BITSTREAM_READ,
                                                      embed=embeds)

    # ------------------------------------------------------------------
    # Mapped items
    # ------------------------------------------------------------------

    def iter_mapped_items(self, collection_uuid: str, all_languages: Optional[bool] = None,
                          embed: EmbedArg = None):
        """Items mapped into (not owned by) the collection. A generator.

        Parameters:
            collection_uuid (str): UUID of the collection
            all_languages (Optional[bool]): Whether to retrieve metadata in all languages
            embed (EmbedArg): Embedding options for the response

        Yields:
            Dict[str, Any]: Mapped item metadata
        """
        url = self._url(Endpoints.COLLECTIONS, f"/{collection_uuid}/mappedItems")
        return self._iter_embedded(url, ("mappedItems", "items"), f"Get mapped items of {collection_uuid}",
                                   all_languages=all_languages, embed=embed)

    def get_mapped_items(self, collection_uuid: str, all_languages: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Get all items mapped into (not owned by) the collection.

        Parameters:
            collection_uuid (str): UUID of the collection
            all_languages (Optional[bool]): Whether to retrieve metadata in all languages

        Returns:
            List[Dict[str, Any]]: List of mapped item metadata
        """
        return list(self.iter_mapped_items(collection_uuid, all_languages))

    def map_item(self, collection_uuid: str, item_uuid: str) -> bool:
        """Show an item in this collection too (no-op if already mapped; 422 if it is the owning collection).

        Parameters:
            collection_uuid (str): UUID of the collection
            item_uuid (str): UUID of the item to map

        Returns:
            bool: True if the item was mapped successfully
        """
        self._request("POST", self._url(Endpoints.ITEMS, f"/{item_uuid}/mappedCollections"),
                      f"Map item {item_uuid} into {collection_uuid}", expected=(200, 201, 204),
                      headers=self._headers("text/uri-list"),
                      data=self._url(Endpoints.COLLECTIONS, f"/{collection_uuid}"))
        return True

    def unmap_item(self, collection_uuid: str, item_uuid: str) -> bool:
        """Unmap an item from this collection.

        Parameters:
            collection_uuid (str): UUID of the collection
            item_uuid (str): UUID of the item to unmap

        Returns:
            bool: True if the item was unmapped successfully
        """
        self._request("DELETE", self._url(Endpoints.ITEMS, f"/{item_uuid}/mappedCollections/{collection_uuid}"),
                      f"Unmap item {item_uuid} from {collection_uuid}", expected=(200, 204))
        return True

    def map_items(self, collection_uuid: str, item_uuids: Iterable[str]) -> Dict[str, str]:
        """Map several items; {item uuid: "mapped" | error message} - one failure does not stop the rest.

        Parameters:
            collection_uuid (str): UUID of the collection
            item_uuids (Iterable[str]): UUIDs of the items to map

        Returns:
            Dict[str, str]: Mapping of item UUIDs to "mapped" or error message
        """
        from .exceptions import DSpaceError
        result = {}
        for item_uuid in item_uuids:
            try:
                self.map_item(collection_uuid, item_uuid)
                result[item_uuid] = "mapped"
            except DSpaceError as exc:
                result[item_uuid] = f"error: {exc}"
        return result

    # ------------------------------------------------------------------
    # OAI-PMH harvesting
    # ------------------------------------------------------------------

    # Harvest types for OAI-PMH harvesting
    HARVEST_TYPES = ("NONE", "METADATA_ONLY", "METADATA_AND_REF", "METADATA_AND_BITSTREAMS")

    def get_harvester_metadata_configs(self) -> List[Dict[str, Any]]:
        """Get harvester metadata configurations.

        Returns:
            List[Dict[str, Any]]: List of harvester metadata configurations, e.g.,
                [{"id": "dc", "label": "Simple Dublin Core", "nameSpace": ...}, ...]
        """
        return self._get_json(self._url("/config/harvestermetadata"), "Get harvester metadata configs").get(
            "configs") or []

    def get_harvester(self, collection_uuid: str) -> Dict[str, Any]:
        """Harvest settings and status (collection admins only).
        
        Parameters:
            collection_uuid (str): UUID of the collection

        Returns:
            Dict[str, Any]: Harvester settings and status
        """
        return self._get_json(self._url(Endpoints.COLLECTIONS, f"/{collection_uuid}/harvester"),
                              f"Get harvester settings of {collection_uuid}")

    def set_harvester(self, collection_uuid: str, oai_source: str, oai_set_id: Optional[str] = None,
                      metadata_config_id: str = "dc", harvest_type: str = "METADATA_ONLY") -> Dict[str, Any]:
        """Turn the collection into an OAI-PMH harvested collection (the server checks the source).

        Parameters:
            collection_uuid (str): UUID of the collection
            oai_source (str): OAI-PMH source URL
            oai_set_id (Optional[str]): OAI-PMH set ID
            metadata_config_id (str): Metadata configuration ID
            harvest_type (str): Harvest type, one of HARVEST_TYPES

        Returns:
            Dict[str, Any]: Updated harvester settings
        """
        if harvest_type not in self.HARVEST_TYPES:
            raise ValueError(f"harvest_type must be one of {self.HARVEST_TYPES}")
        body = {"harvest_type": harvest_type, "oai_source": oai_source, "oai_set_id": oai_set_id,
                "metadata_config_id": metadata_config_id}
        return self._put_harvester(collection_uuid, body)

    def disable_harvesting(self, collection_uuid: str) -> Dict[str, Any]:
        """Disable OAI-PMH harvesting for the collection.

        Parameters:
            collection_uuid (str): UUID of the collection

        Returns:
            Dict[str, Any]: Updated harvester settings
        """
        return self._put_harvester(collection_uuid, {"harvest_type": "NONE"})

    def _put_harvester(self, collection_uuid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Update harvester settings for the collection.

        Parameters:
            collection_uuid (str): UUID of the collection
            body (Dict[str, Any]): Request body containing harvester settings

        Returns:
            Dict[str, Any]: Updated harvester settings
        """
        response = self._request("PUT", self._url(Endpoints.COLLECTIONS, f"/{collection_uuid}/harvester"),
                                 f"Update harvester settings of {collection_uuid}", expected=(200, 204),
                                 headers=self._headers(), json=body)
        return self._json_or_none(response) or self.get_harvester(collection_uuid)
