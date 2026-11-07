# -*- coding: UTF-8 -*-
"""
Permission (resource policy) management for DSpace API (DSpace 7+ / DSpace-CRIS 7+)

In DSpace every permission is a *resource policy*: "group G (or eperson E)
may perform ACTION on resource R, optionally between startDate and endDate".
Items, bundles and bitstreams each carry their own policies, so e.g. hiding
an item from the public means removing the Anonymous READ policy from the
item AND, if the files should be hidden too, from its bundles/bitstreams.

Resource policies
    GET    /authz/resourcepolicies/{id}
    GET    /authz/resourcepolicies/search/resource?uuid=&action=
    GET    /authz/resourcepolicies/search/group?uuid=&resource=
    GET    /authz/resourcepolicies/search/eperson?uuid=&resource=
    POST   /authz/resourcepolicies?resource=&group=|eperson=
    PATCH  /authz/resourcepolicies/{id}               name, description, dates
    DELETE /authz/resourcepolicies/{id}
    GET|PUT /authz/resourcepolicies/{id}/group|eperson
    GET    /authz/resourcepolicies/{id}/resource

Groups (lookup only)
    GET    /eperson/groups/{uuid}
    GET    /eperson/groups/search/byMetadata?query=

Bulk access control (DSpace 7.6+)
    GET    /config/bulkaccessconditionoptions[/{name}]
    POST   /system/scripts/bulk-access-control/processes
    GET    /system/processes/{id}

Typical use
-----------
    perms = PermissionManager(client.auth)

    # Dry run: what would be removed?
    rows = perms.remove_group_access_from_item(item_uuid, include_bitstreams=True)

    # Really remove Anonymous READ from item, bundles and bitstreams
    rows = perms.remove_group_access_from_item(item_uuid, include_bitstreams=True, dry_run=False)

    # Undo, using the rows (e.g. re-read from a CSV report)
    perms.restore_policies(rows, dry_run=False)

Notes
-----
- Removing Anonymous READ from existing items does not change the
  collection's DEFAULT_ITEM_READ / DEFAULT_BITSTREAM_READ policies, so new
  items deposited into that collection will still be public.
- A bitstream's download permission is its own READ policy. Removing READ
  from the item alone hides the item page, not necessarily its files.
- All write methods default to dry_run=True where they remove things.
"""

import csv
import json
import logging
import time
from datetime import date, datetime
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from ._base import BaseManager, XSRF_RESPONSE_HEADER
from .config import Endpoints
from .exceptions import DSpaceError, NotFoundError
from .utils import validate_uuid

# Logger for the module
logger = logging.getLogger(__name__)

# API endpoints for DSpace permissions and groups
RESOURCE_POLICIES = getattr(Endpoints, "RESOURCE_POLICIES", "/authz/resourcepolicies")
GROUPS = getattr(Endpoints, "GROUPS", "/eperson/groups")
EPERSONS = getattr(Endpoints, "EPERSONS", "/eperson/epersons")
BULK_ACCESS_CONDITION_OPTIONS = getattr(Endpoints, "BULK_ACCESS_CONDITION_OPTIONS",
                                        "/config/bulkaccessconditionoptions")
SCRIPTS = getattr(Endpoints, "SCRIPTS", "/system/scripts")
PROCESSES = getattr(Endpoints, "PROCESSES", "/system/processes")
DISCOVER_SEARCH = getattr(Endpoints, "DISCOVER_SEARCH", "/discover/search/objects")

# Built-in group names
ANONYMOUS = "Anonymous"
ADMINISTRATOR = "Administrator"


class Actions:
    """Resource policy actions (org.dspace.core.Constants)"""
    READ = "READ"
    WRITE = "WRITE"
    ADD = "ADD"
    REMOVE = "REMOVE"
    ADMIN = "ADMIN"
    DELETE = "DELETE"
    WITHDRAWN_READ = "WITHDRAWN_READ"
    DEFAULT_BITSTREAM_READ = "DEFAULT_BITSTREAM_READ"
    DEFAULT_ITEM_READ = "DEFAULT_ITEM_READ"

    ALL = (READ, WRITE, ADD, REMOVE, ADMIN, DELETE, WITHDRAWN_READ,
           DEFAULT_BITSTREAM_READ, DEFAULT_ITEM_READ)


class PolicyTypes:
    # Policy types used in DSpace resource policies
    SUBMISSION = "TYPE_SUBMISSION"
    WORKFLOW = "TYPE_WORKFLOW"
    INHERITED = "TYPE_INHERITED"
    CUSTOM = "TYPE_CUSTOM"

    ALL = (SUBMISSION, WORKFLOW, INHERITED, CUSTOM)


# Columns of the rows returned by remove_*/restore_* (and of CSV reports)
POLICY_ROW_FIELDS = [
    "item_uuid", "resource_type", "resource_uuid", "resource_name",
    "policy_id", "action", "policy_type", "group_name", "group_uuid",
    "name", "description", "start_date", "end_date", "status",
]

# Internal helpers and type definitions for permission management
_UNSET = object()
DateLike = Union[None, str, date, datetime]


def _fmt_date(value: DateLike) -> Optional[str]:
    """Normalise to YYYY-MM-DD (the format DSpace expects for policy dates).
    
    Parameters:
        value (DateLike): The date value to normalise.

    Returns:
        Optional[str]: The normalised date string in YYYY-MM-DD format, or None if the input is None or empty.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    value = str(value).strip()
    return value[:10]


def _as_list(value) -> Optional[List[str]]:
    """Convert a value to a list of strings.
    
    Parameters:
        value (Any): The value to convert to a list.

    Returns:
        Optional[List[str]]: A list of strings if the input is not None, otherwise None.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return list(value)


class PermissionManager(BaseManager):
    """Manages DSpace resource policies, group lookups and bulk access control"""

    def __init__(self, auth):
        """Initialize the PermissionManager with the given authentication context."""
        super().__init__(auth)
        self._group_cache: Dict[str, Dict[str, Any]] = {}
        self._bundle_manager = None

    @property
    def bundles(self):
        """Get the bundle manager, initializing it if necessary."""
        if self._bundle_manager is None:
            from .bundles import BundleManager  # local import avoids a hard dependency at import time
            self._bundle_manager = BundleManager(self.auth)
        return self._bundle_manager

    # ------------------------------------------------------------------
    # Resource policies: read
    # ------------------------------------------------------------------

    def get_policy(self, policy_id: int, embed: Sequence[str] = ("group", "eperson")) -> Dict[str, Any]:
        """Retrieve a specific resource policy by its ID, optionally embedding related group and eperson information.

        Parameters:
            policy_id (int): The ID of the resource policy to retrieve.
            embed (Sequence[str], optional): The related entities to embed in the response.

        Returns:
            Dict[str, Any]: The resource policy data.
        """
        url = self._url(RESOURCE_POLICIES, f"/{policy_id}")
        params = [("embed", e) for e in embed]
        return self._request("GET", url, f"Get resource policy {policy_id}", params=params).json()

    def get_resource_policies(self, resource_uuid: str, action: Optional[str] = None,
                              embed: Sequence[str] = ("group", "eperson")) -> List[Dict[str, Any]]:
        """All policies attached to a DSpace object (item, bundle, bitstream, collection, ...).

        Parameters:
            resource_uuid (str): The UUID of the resource.
            action (Optional[str], optional): The action to filter policies by.
            embed (Sequence[str], optional): The related entities to embed in the response.

        Returns:
            List[Dict[str, Any]]: The list of resource policies for the specified resource.
        """
        params: List[Tuple[str, Any]] = [("uuid", resource_uuid)]
        if action:
            params.append(("action", action))
        params += [("embed", e) for e in embed]
        url = self._url(RESOURCE_POLICIES, "/search/resource")
        return list(self._iter_embedded(url, "resourcepolicies",
                                        f"Get policies of resource {resource_uuid}", params))

    def get_group_policies(self, group_uuid: str, resource_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Policies granted directly to a group (optionally limited to one resource).

        Parameters:
            group_uuid (str): The UUID of the group.
            resource_uuid (Optional[str], optional): The UUID of the resource to filter by.

        Returns:
            List[Dict[str, Any]]: The list of resource policies for the specified group.
        """
        params: List[Tuple[str, Any]] = [("uuid", group_uuid)]
        if resource_uuid:
            params.append(("resource", resource_uuid))
        url = self._url(RESOURCE_POLICIES, "/search/group")
        return list(self._iter_embedded(url, "resourcepolicies", f"Get policies of group {group_uuid}", params))

    def get_eperson_policies(self, eperson_uuid: str, resource_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Policies granted directly to an eperson (not via group membership).

        Parameters:
            eperson_uuid (str): The UUID of the eperson.
            resource_uuid (Optional[str], optional): The UUID of the resource to filter by.

        Returns:
            List[Dict[str, Any]]: The list of resource policies for the specified eperson.
        """
        params: List[Tuple[str, Any]] = [("uuid", eperson_uuid)]
        if resource_uuid:
            params.append(("resource", resource_uuid))
        url = self._url(RESOURCE_POLICIES, "/search/eperson")
        return list(self._iter_embedded(url, "resourcepolicies", f"Get policies of eperson {eperson_uuid}", params))

    def get_policy_group(self, policy_id: int) -> Optional[Dict[str, Any]]:
        """Get the group associated with a specific resource policy.

        Parameters:
            policy_id (int): The ID of the resource policy.

        Returns:
            Optional[Dict[str, Any]]: The group data if available, otherwise None.
        """
        url = self._url(RESOURCE_POLICIES, f"/{policy_id}/group")
        r = self._request("GET", url, f"Get group of policy {policy_id}", expected=(200, 204))
        return r.json() if r.status_code == 200 and r.content else None

    def get_policy_eperson(self, policy_id: int) -> Optional[Dict[str, Any]]:
        """Get the eperson associated with a specific resource policy.

        Parameters:
            policy_id (int): The ID of the resource policy.

        Returns:
            Optional[Dict[str, Any]]: The eperson data if available, otherwise None.
        """
        url = self._url(RESOURCE_POLICIES, f"/{policy_id}/eperson")
        r = self._request("GET", url, f"Get eperson of policy {policy_id}", expected=(200, 204))
        return r.json() if r.status_code == 200 and r.content else None

    def get_policy_resource(self, policy_id: int) -> Dict[str, Any]:
        """Get the resource associated with a specific resource policy.

        Parameters:
            policy_id (int): The ID of the resource policy.

        Returns:
            Dict[str, Any]: The resource data.
        """
        url = self._url(RESOURCE_POLICIES, f"/{policy_id}/resource")
        return self._request("GET", url, f"Get resource of policy {policy_id}").json()

    # ------------------------------------------------------------------
    # Resource policies: write
    # ------------------------------------------------------------------

    def create_policy(self, resource_uuid: str, action: str = Actions.READ,
                      group: Optional[str] = None, eperson_uuid: Optional[str] = None,
                      policy_type: Optional[str] = PolicyTypes.CUSTOM,
                      name: Optional[str] = None, description: Optional[str] = None,
                      start_date: DateLike = None, end_date: DateLike = None) -> Dict[str, Any]:
        """
        Create a policy on a resource for exactly one of group / eperson.

        group may be a group UUID or a group name (e.g. "Anonymous").
        start_date / end_date: 'YYYY-MM-DD', date or datetime (None = unlimited).
        Only system administrators can create policies.

        Parameters:
            resource_uuid (str): The UUID of the resource to create the policy on.
            action (str, optional): The action for the policy (default is Actions.READ).
            group (Optional[str], optional): The UUID or name of the group to assign the policy to.
            eperson_uuid (Optional[str], optional): The UUID of the eperson to assign the policy to.
            policy_type (Optional[str], optional): The type of the policy (default is PolicyTypes.CUSTOM).
            name (Optional[str], optional): The name of the policy.
            description (Optional[str], optional): The description of the policy.
            start_date (DateLike, optional): The start date of the policy.
            end_date (DateLike, optional): The end date of the policy.

        Returns:
            Dict[str, Any]: The created policy data.
        
        """
        if bool(group) == bool(eperson_uuid):
            raise ValueError("Pass exactly one of group or eperson_uuid")
        if action not in Actions.ALL:
            raise ValueError(f"Unknown action {action!r}; expected one of {Actions.ALL}")
        if policy_type is not None and policy_type not in PolicyTypes.ALL:
            raise ValueError(f"Unknown policy_type {policy_type!r}; expected one of {PolicyTypes.ALL}")

        params: List[Tuple[str, Any]] = [("resource", resource_uuid)]
        if group:
            # a UUID needs no lookup; names are resolved (and cached) via the API
            params.append(("group", group if validate_uuid(group) else self.resolve_group(group)["uuid"]))
        else:
            params.append(("eperson", eperson_uuid))

        body = {
            "name": name,
            "description": description,
            "policyType": policy_type,
            "action": action,
            "startDate": _fmt_date(start_date),
            "endDate": _fmt_date(end_date),
            "type": "resourcepolicy",
        }
        url = self._url(RESOURCE_POLICIES)
        policy = self._request("POST", url, f"Create {action} policy on {resource_uuid}",
                               expected=(200, 201), headers=self._headers(),
                               params=params, json=body).json()
        logger.info("Created policy %s (%s) on %s", policy.get("id"), action, resource_uuid)
        return policy

    def patch_policy(self, policy_id: int, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Raw JSON Patch on a policy (paths: /name, /description, /startDate, /endDate).

        Parameters:
            policy_id (int): The ID of the resource policy to patch.
            operations (List[Dict[str, Any]]): The list of JSON Patch operations to apply.

        Returns:
            Dict[str, Any]: The updated policy data.
        """
        url = self._url(RESOURCE_POLICIES, f"/{policy_id}")
        return self._request("PATCH", url, f"Patch resource policy {policy_id}",
                             headers=self._headers("application/json"), json=operations).json()

    def update_policy(self, policy_id: int, name=_UNSET, description=_UNSET,
                      start_date=_UNSET, end_date=_UNSET) -> Dict[str, Any]:
        """
        Set or clear policy name / description / dates. Arguments left out are
        untouched; None clears the field (e.g. end_date=None lifts a lease,
        start_date=None lifts an embargo).

        Parameters:
            policy_id (int): The ID of the resource policy to update.
            name (Optional[str], optional): The new name for the policy.
            description (Optional[str], optional): The new description for the policy.
            start_date (DateLike, optional): The new start date for the policy.
            end_date (DateLike, optional): The new end date for the policy.

        Returns:
            Dict[str, Any]: The updated policy data.
        """
        current = self.get_policy(policy_id, embed=())
        ops = []
        for path, value in (("name", name), ("description", description),
                            ("startDate", start_date), ("endDate", end_date)):
            if value is _UNSET:
                continue
            if path in ("startDate", "endDate"):
                value = _fmt_date(value)
            if value is None:
                if current.get(path) is not None:   # remove on an empty field is an error
                    ops.append({"op": "remove", "path": f"/{path}"})
            else:
                ops.append({"op": "add", "path": f"/{path}", "value": value})
        if not ops:
            return current
        logger.info("Updating policy %s: %s", policy_id, ops)
        return self.patch_policy(policy_id, ops)

    def delete_policy(self, policy_id: int, missing_ok: bool = False) -> bool:
        """Delete one policy. With missing_ok, a 404 (already deleted) returns False instead of raising.

        Parameters:
            policy_id (int): The ID of the resource policy to delete.
            missing_ok (bool, optional): If True, a 404 response will return False instead of raising an error.

        Returns:
            bool: True if the policy was deleted, False if it was already missing and missing_ok is True.
        """
        url = self._url(RESOURCE_POLICIES, f"/{policy_id}")
        try:
            self._request("DELETE", url, f"Delete resource policy {policy_id}", expected=(200, 204))
        except NotFoundError:
            if missing_ok:
                logger.info("Policy %s was already gone", policy_id)
                return False
            raise
        logger.debug("Deleted policy %s", policy_id)
        return True

    def set_policy_group(self, policy_id: int, group: str) -> bool:
        """Re-assign a group policy to another group (name or UUID).

        Parameters:
            policy_id (int): The ID of the resource policy to update.
            group (str): The UUID or name of the group to assign the policy to.

        Returns:
            bool: True if the operation was successful.
        """
        group_uuid = self.resolve_group(group)["uuid"]
        url = self._url(RESOURCE_POLICIES, f"/{policy_id}/group")
        self._request("PUT", url, f"Set group of policy {policy_id}", expected=(200, 204),
                      headers=self._headers("text/uri-list"), data=self._url(GROUPS, f"/{group_uuid}"))
        return True

    def set_policy_eperson(self, policy_id: int, eperson_uuid: str) -> bool:
        """Re-assign an eperson policy to another eperson by UUID.

        Parameters:
            policy_id (int): The ID of the resource policy to update.
            eperson_uuid (str): The UUID of the eperson to assign the policy to.

        Returns:
            bool: True if the operation was successful.
        """
        url = self._url(RESOURCE_POLICIES, f"/{policy_id}/eperson")
        self._request("PUT", url, f"Set eperson of policy {policy_id}", expected=(200, 204),
                      headers=self._headers("text/uri-list"), data=self._url(EPERSONS, f"/{eperson_uuid}"))
        return True

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def get_group(self, group_uuid: str) -> Dict[str, Any]:
        """Retrieve a group by its UUID.

        Parameters:
            group_uuid (str): The UUID of the group to retrieve.

        Returns:
            Dict[str, Any]: The group data.
        """
        url = self._url(GROUPS, f"/{group_uuid}")
        return self._request("GET", url, f"Get group {group_uuid}").json()

    def find_group_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find a group by its exact (case-insensitive) name via byMetadata search.

        Parameters:
            name (str): The name of the group to search for.

        Returns:
            Optional[Dict[str, Any]]: The group data if found, else None.
        """
        url = self._url(GROUPS, "/search/byMetadata")
        for group in self._iter_embedded(url, "groups", f"Search group '{name}'", [("query", name)]):
            if (group.get("name") or "").lower() == name.lower():
                return group
        return None

    def resolve_group(self, group: str) -> Dict[str, Any]:
        """Accept a group UUID or name, return the group (cached).

        Parameters:
            group (str): The UUID or name of the group to resolve.

        Returns:
            Dict[str, Any]: The resolved group data.

        Raises:
            NotFoundError: If the group cannot be found.
        """
        key = group.lower()
        if key not in self._group_cache:
            found = self.get_group(group) if validate_uuid(group) else self.find_group_by_name(group)
            if not found:
                raise NotFoundError(f"Group {group!r} not found")
            self._group_cache[key] = found
        return self._group_cache[key]

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def policy_group(self, policy: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The group of a policy: from the embed if present, else one extra request.

        Parameters:
            policy (Dict[str, Any]): The policy data.

        Returns:
            Optional[Dict[str, Any]]: The group data if found, else None.
        """
        embedded = policy.get("_embedded") or {}
        if "group" in embedded:
            return embedded["group"]
        return self.get_policy_group(policy["id"])

    def filter_policies(self, policies: Iterable[Dict[str, Any]], group: Optional[str] = None,
                        actions: Optional[Sequence[str]] = None,
                        policy_types: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """
        Keep the policies matching all given criteria.

        Parameters:
            policies (Iterable[Dict[str, Any]]): The list of policies to filter.
            group (Optional[str], optional): The group name or UUID to filter by. Policies granted to epersons never match. Defaults to None.
            actions (Optional[Sequence[str]], optional): The list of actions to filter by. None means any. Defaults to None.
            policy_types (Optional[Sequence[str]], optional): The list of policy types to filter by. None means any. Defaults to None.

        Returns:
            List[Dict[str, Any]]: The list of policies matching all given criteria.
        """
        actions = _as_list(actions)
        policy_types = _as_list(policy_types)
        is_uuid = bool(group) and validate_uuid(group)
        result = []
        for p in policies:
            if actions and p.get("action") not in actions:
                continue
            if policy_types and p.get("policyType") not in policy_types:
                continue
            if group:
                g = self.policy_group(p) or {}
                if is_uuid:
                    if g.get("uuid") != group:
                        continue
                elif (g.get("name") or "").lower() != group.lower():
                    continue
            result.append(p)
        return result

    def find_group_policies_on_resource(self, resource_uuid: str, group: str = ANONYMOUS,
                                        actions: Optional[Sequence[str]] = (Actions.READ,)) -> List[Dict[str, Any]]:
        """Find policies for a group on a specific resource.

        Parameters:
            resource_uuid (str): The UUID of the resource.
            group (str, optional): The group name or UUID. Defaults to ANONYMOUS.
            actions (Optional[Sequence[str]], optional): The list of actions to filter by. Defaults to (Actions.READ,).

        Returns:
            List[Dict[str, Any]]: The list of policies matching the criteria.
        """
        return self.filter_policies(self.get_resource_policies(resource_uuid), group=group, actions=actions)

    def has_group_access(self, resource_uuid: str, group: str = ANONYMOUS, action: str = Actions.READ,
                         on_date: DateLike = None) -> bool:
        """Check if a group has access to a resource for a specific action on a given date.

        Parameters:
            resource_uuid (str): The UUID of the resource.
            group (str, optional): The group name or UUID. Defaults to ANONYMOUS.
            action (str, optional): The action to check. Defaults to Actions.READ.
            on_date (DateLike, optional): The date to check access for. Defaults to today.

        Returns:
            bool: True if a policy for the group+action exists and is valid on on_date, False otherwise.
        """
        day = _fmt_date(on_date) or date.today().isoformat()
        for p in self.find_group_policies_on_resource(resource_uuid, group, [action]):
            start, end = _fmt_date(p.get("startDate")), _fmt_date(p.get("endDate"))
            if (start is None or start <= day) and (end is None or end >= day):
                return True
        return False

    # ------------------------------------------------------------------
    # Targets: an item and its bundles / bitstreams, or all items in a scope
    # ------------------------------------------------------------------

    def iter_item_resources(self, item_uuid: str, item_name: str = "", include_bundles: bool = True,
                            include_bitstreams: bool = True) -> Iterator[Dict[str, str]]:
        """
        Yield {"type", "uuid", "name"} for the item and (optionally) its bundles
        and bitstreams. Bundles and bitstreams come from one embedded request
        (BundleManager.get_item_with_files) instead of 1 + one per bundle.

        Parameters:
            item_uuid (str): The UUID of the item.
            item_name (str, optional): The name of the item. Defaults to "".
            include_bundles (bool, optional): Whether to include bundles. Defaults to True.
            include_bitstreams (bool, optional): Whether to include bitstreams. Defaults to True.

        Yields:
            Dict[str, str]: A dictionary with keys "type", "uuid", and "name" for each resource.
        """
        if not (include_bundles or include_bitstreams):
            yield {"type": "item", "uuid": item_uuid, "name": item_name}
            return
        item, bundles = self.bundles.get_item_with_files(item_uuid)
        yield {"type": "item", "uuid": item_uuid, "name": item_name or item.get("name") or ""}
        for bundle in bundles:
            if include_bundles:
                yield {"type": "bundle", "uuid": bundle["uuid"], "name": bundle.get("name") or ""}
            if include_bitstreams:
                for bs in bundle["bitstreams"]:
                    yield {"type": "bitstream", "uuid": bs["uuid"], "name": bs.get("name") or ""}

    def iter_items_in_scope(self, scope_uuid: str, page_size: int = 100) -> Iterator[Dict[str, Any]]:
        """
        Yield every item in a collection/community via discovery.

        Discovery reflects the search index: run it as an administrator so
        private/withdrawn items are included, and collect the list before
        changing policies (changes are re-indexed and may shift pages).

        Parameters:
            scope_uuid (str): The UUID of the collection or community scope.
            page_size (int, optional): The number of items to fetch per page. Defaults to 100.

        Yields:
            Dict[str, Any]: The item data for each item in the scope.
        """
        url = self._url(DISCOVER_SEARCH)
        page = 0
        while True:
            params = [("scope", scope_uuid), ("dsoType", "ITEM"), ("page", page), ("size", page_size)]
            data = self._request("GET", url, f"List items in scope {scope_uuid}", params=params).json()
            result = data.get("_embedded", {}).get("searchResult", {})
            objects = result.get("_embedded", {}).get("objects", [])
            for obj in objects:
                item = obj.get("_embedded", {}).get("indexableObject")
                if item:
                    yield item
            total_pages = (result.get("page") or {}).get("totalPages", 0)
            page += 1
            if page >= total_pages or not objects:
                break

    # ------------------------------------------------------------------
    # High level: remove / grant / restore group access
    # ------------------------------------------------------------------

    def _policy_row(self, resource: Dict[str, str], policy: Dict[str, Any], item_uuid: str = "") -> Dict[str, Any]:
        """
        Construct a dictionary representing a policy row.

        Parameters:
            resource (Dict[str, str]): The resource dictionary with keys "type", "uuid", and "name".
            policy (Dict[str, Any]): The policy dictionary.
            item_uuid (str, optional): The UUID of the item. Defaults to "".

        Returns:
            Dict[str, Any]: A dictionary representing the policy row.
        """ 
        g = self.policy_group(policy) or {}
        return {
            "item_uuid": item_uuid,
            "resource_type": resource.get("type", ""),
            "resource_uuid": resource["uuid"],
            "resource_name": resource.get("name", ""),
            "policy_id": policy.get("id"),
            "action": policy.get("action"),
            "policy_type": policy.get("policyType"),
            "group_name": g.get("name"),
            "group_uuid": g.get("uuid"),
            "name": policy.get("name"),
            "description": policy.get("description"),
            "start_date": policy.get("startDate"),
            "end_date": policy.get("endDate"),
            "status": "",
        }

    def remove_group_policies(self, resources: Iterable[Union[str, Dict[str, str]]],
                              group: str = ANONYMOUS,
                              actions: Optional[Sequence[str]] = (Actions.READ,),
                              policy_types: Optional[Sequence[str]] = None,
                              dry_run: bool = True, item_uuid: str = "") -> List[Dict[str, Any]]:
        """
        Remove the group's policies from the given resources.

        resources: UUIDs or dicts {"type", "uuid", "name"} (see iter_item_resources)
        actions: None = every action of the group
        Returns one row per matching policy (fields: POLICY_ROW_FIELDS) with
        status would-delete | deleted | already-gone | failed:<reason>.
        Each row holds everything restore_policies() needs to undo it.

        Parameters:
            resources (Iterable[Union[str, Dict[str, str]]]): The list of resource UUIDs or dictionaries with keys "type", "uuid", and "name".
            group (str, optional): The group name or UUID. Defaults to ANONYMOUS.
            actions (Optional[Sequence[str]], optional): The list of actions to filter by. Defaults to (Actions.READ,).
            policy_types (Optional[Sequence[str]], optional): The list of policy types to filter by. Defaults to None.
            dry_run (bool, optional): If True, only simulate the removal. Defaults to True.
            item_uuid (str, optional): The UUID of the item. Defaults to "".

        Returns:
            List[Dict[str, Any]]: A list of dictionaries representing the policy rows with their status.
        """
        rows = []
        for res in resources:
            resource = res if isinstance(res, dict) else {"type": "", "uuid": res, "name": ""}
            matches = self.filter_policies(self.get_resource_policies(resource["uuid"]),
                                           group=group, actions=actions, policy_types=policy_types)
            for policy in matches:
                row = self._policy_row(resource, policy, item_uuid)
                label = f"{row['resource_type'] or 'resource'} {resource['uuid']} '{row['resource_name']}'"
                if dry_run:
                    row["status"] = "would-delete"
                    logger.info("[dry-run] Would delete policy %s (%s, %s, group %s) on %s",
                                row["policy_id"], row["action"], row["policy_type"], row["group_name"], label)
                else:
                    try:
                        deleted = self.delete_policy(row["policy_id"], missing_ok=True)
                        row["status"] = "deleted" if deleted else "already-gone"
                        logger.info("Deleted policy %s (%s, group %s) on %s",
                                    row["policy_id"], row["action"], row["group_name"], label)
                    except DSpaceError as exc:
                        row["status"] = f"failed:{getattr(exc, 'status_code', '') or exc}"
                        logger.error("Could not delete policy %s on %s: %s", row["policy_id"], label, exc)
                rows.append(row)
        return rows

    def remove_group_access_from_item(self, item_uuid: str, group: str = ANONYMOUS,
                                      actions: Optional[Sequence[str]] = (Actions.READ,),
                                      include_bundles: bool = False, include_bitstreams: bool = False,
                                      item_name: str = "", dry_run: bool = True) -> List[Dict[str, Any]]:
        """
        Remove the group's policies from an item and, optionally, its bundles
        and bitstreams. Default: Anonymous READ on the item only, dry run.
        include_bitstreams=True is usually what you want to also block downloads.

        Parameters:
            item_uuid (str): The UUID of the item.
            group (str, optional): The group name or UUID. Defaults to ANONYMOUS.
            actions (Optional[Sequence[str]], optional): The list of actions to remove. Defaults to (Actions.READ,).
            include_bundles (bool, optional): If True, include the item's bundles. Defaults to False.
            include_bitstreams (bool, optional): If True, include the item's bitstreams. Defaults to False.
            item_name (str, optional): The name of the item. Defaults to "".
            dry_run (bool, optional): If True, only simulate the removal. Defaults to True.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries representing the policy rows with their status.
        """
        resources = list(self.iter_item_resources(item_uuid, item_name, include_bundles, include_bitstreams))
        return self.remove_group_policies(resources, group=group, actions=actions,
                                          dry_run=dry_run, item_uuid=item_uuid)

    def grant_group_access(self, resource_uuid: str, group: str = ANONYMOUS, action: str = Actions.READ,
                           start_date: DateLike = None, end_date: DateLike = None,
                           policy_type: Optional[str] = PolicyTypes.CUSTOM,
                           name: Optional[str] = None, description: Optional[str] = None,
                           skip_if_exists: bool = True) -> Dict[str, Any]:
        """
        Give a group an action on a resource (e.g. make an item public again,
        or set an embargo via start_date). With skip_if_exists, an existing
        policy with the same group/action/dates is returned instead of adding a duplicate.

        Parameters:
            resource_uuid (str): The UUID of the resource.
            group (str, optional): The group name or UUID. Defaults to ANONYMOUS.
            action (str, optional): The action to grant. Defaults to Actions.READ.
            start_date (DateLike, optional): The start date of the policy. Defaults to None.
            end_date (DateLike, optional): The end date of the policy. Defaults to None.
            policy_type (Optional[str], optional): The type of the policy. Defaults to PolicyTypes.CUSTOM.
            name (Optional[str], optional): The name of the policy. Defaults to None.
            description (Optional[str], optional): The description of the policy. Defaults to None.
            skip_if_exists (bool, optional): If True, skip creating the policy if it already exists. Defaults to True.

        Returns:
            Dict[str, Any]: The created or existing policy.
        """
        if skip_if_exists:
            want = (_fmt_date(start_date), _fmt_date(end_date))
            for p in self.find_group_policies_on_resource(resource_uuid, group, [action]):
                if (_fmt_date(p.get("startDate")), _fmt_date(p.get("endDate"))) == want:
                    logger.info("Policy %s already grants %s to %s on %s", p.get("id"), action, group, resource_uuid)
                    return p
        return self.create_policy(resource_uuid, action=action, group=group, policy_type=policy_type,
                                  name=name, description=description, start_date=start_date, end_date=end_date)

    def restore_policies(self, rows: Iterable[Dict[str, Any]], dry_run: bool = True,
                         statuses: Sequence[str] = ("deleted",)) -> List[Dict[str, Any]]:
        """
        Re-create policies from rows produced by remove_group_policies()
        (or read back from its CSV report). Only rows whose status is in
        `statuses` are restored; the new policy gets a new id.

        Parameters:
            rows (Iterable[Dict[str, Any]]): The list of policy rows to restore.
            dry_run (bool, optional): If True, only simulate the restoration. Defaults to True.
            statuses (Sequence[str], optional): The list of statuses to restore. Defaults to ("deleted",).

        Returns:
            List[Dict[str, Any]]: A list of dictionaries representing the restored policy rows with their status.
        """
        results = []
        for row in rows:
            if row.get("status") not in statuses:
                continue
            out = dict(row)
            group = row.get("group_uuid") or row.get("group_name")
            if not group:
                out["status"] = "restore-skipped:no-group"
                results.append(out)
                continue
            if dry_run:
                out["status"] = "would-restore"
                logger.info("[dry-run] Would restore %s for %s on %s %s",
                            row.get("action"), row.get("group_name") or group,
                            row.get("resource_type"), row.get("resource_uuid"))
            else:
                try:
                    policy = self.grant_group_access(
                        row["resource_uuid"], group=group, action=row.get("action") or Actions.READ,
                        start_date=row.get("start_date") or None, end_date=row.get("end_date") or None,
                        policy_type=row.get("policy_type") or None,
                        name=row.get("name") or None, description=row.get("description") or None,
                    )
                    out["status"] = "restored"
                    out["policy_id"] = policy.get("id")
                except DSpaceError as exc:
                    out["status"] = f"restore-failed:{getattr(exc, 'status_code', '') or exc}"
                    logger.error("Could not restore policy on %s: %s", row.get("resource_uuid"), exc)
            results.append(out)
        return results

    # ------------------------------------------------------------------
    # CSV reports
    # ------------------------------------------------------------------

    @staticmethod
    def write_report(rows: Iterable[Dict[str, Any]], path) -> None:
        """
        Write a CSV report of policy rows.

        Parameters:
            rows (Iterable[Dict[str, Any]]): The list of policy rows to write to the report.
            path (str): The file path to write the CSV report to.
        """
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=POLICY_ROW_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def read_report(path) -> List[Dict[str, Any]]:
        """
        Read a CSV report of policy rows.

        Parameters:
            path (str): The file path to read the CSV report from.

        Returns:
            List[Dict[str, Any]]: The list of policy rows read from the report.
        """
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    # ------------------------------------------------------------------
    # Bulk access control (DSpace 7.6+)
    # ------------------------------------------------------------------

    def list_bulk_access_condition_options(self) -> List[Dict[str, Any]]:
        """
        List the available bulk access condition options.

        Returns:
            List[Dict[str, Any]]: The list of bulk access condition options.
        """
        url = self._url(BULK_ACCESS_CONDITION_OPTIONS)
        return list(self._iter_embedded(url, "bulkaccessconditionoptions", "List bulk access condition options"))

    def get_bulk_access_condition_options(self, config_name: str = "default") -> Dict[str, Any]:
        """
        The access conditions an admin may apply, e.g.
        {"itemAccessConditionOptions": [{"name": "openaccess"}, {"name": "embargo", "hasStartDate": true, ...}],
         "bitstreamAccessConditionOptions": [...]}
        """
        url = self._url(BULK_ACCESS_CONDITION_OPTIONS, f"/{config_name}")
        return self._request("GET", url, f"Get bulk access condition options '{config_name}'").json()

    @staticmethod
    def _validate_conditions(conditions: List[Dict[str, Any]], options: List[Dict[str, Any]], level: str):
        """
        Validate the given access conditions against the available options.

        Parameters:
            conditions (List[Dict[str, Any]]): The list of access conditions to validate.
            options (List[Dict[str, Any]]): The list of available access condition options.
            level (str): The level of access being validated (e.g., "item" or "bitstream").

        Raises:
            ValueError: If any of the conditions are invalid or missing required fields.
        """
        by_name = {o["name"]: o for o in options}
        for cond in conditions:
            name = cond.get("name")
            opt = by_name.get(name)
            if not opt:
                raise ValueError(f"{level} access condition {name!r} not allowed; options: {sorted(by_name)}")
            start, end = _fmt_date(cond.get("startDate")), _fmt_date(cond.get("endDate"))
            if opt.get("hasStartDate") and not start:
                raise ValueError(f"{level} access condition {name!r} requires startDate")
            if opt.get("hasEndDate") and not end:
                raise ValueError(f"{level} access condition {name!r} requires endDate")
            if start and opt.get("maxStartDate") and start > _fmt_date(opt["maxStartDate"]):
                raise ValueError(f"{level} {name!r}: startDate {start} is after maxStartDate {opt['maxStartDate']}")
            if end and opt.get("maxEndDate") and end > _fmt_date(opt["maxEndDate"]):
                raise ValueError(f"{level} {name!r}: endDate {end} is after maxEndDate {opt['maxEndDate']}")
            if start and end and end <= start:
                raise ValueError(f"{level} {name!r}: endDate must be after startDate")

    def build_bulk_access_payload(self, item_conditions: Optional[List[Dict[str, Any]]] = None,
                                  item_mode: str = "replace",
                                  bitstream_conditions: Optional[List[Dict[str, Any]]] = None,
                                  bitstream_mode: str = "replace",
                                  bitstream_uuids: Optional[List[str]] = None,
                                  validate: bool = True, config_name: str = "default") -> Dict[str, Any]:
        """
        Build the JSON file for the bulk-access-control script.

        conditions: e.g. [{"name": "administrator"}] or
                    [{"name": "embargo", "startDate": "2027-01-01"}]
        mode: "replace" drops existing custom policies first, "add" keeps them.
        bitstream_uuids: restrict the bitstream part to these bitstreams (default: all).

        Parameters:
            item_conditions (Optional[List[Dict[str, Any]]]): The list of access conditions for items.
            item_mode (str): The mode for item access conditions ("replace" or "add").
            bitstream_conditions (Optional[List[Dict[str, Any]]]): The list of access conditions for bitstreams.
            bitstream_mode (str): The mode for bitstream access conditions ("replace" or "add").
            bitstream_uuids (Optional[List[str]]): The list of bitstream UUIDs to restrict the bitstream part to.
            validate (bool): Whether to validate the access conditions against the available options.
            config_name (str): The name of the configuration to use for validation.

        Returns:
            Dict[str, Any]: The payload for the bulk-access-control script.
        """
        if item_conditions is None and bitstream_conditions is None:
            raise ValueError("Give item_conditions and/or bitstream_conditions")
        for mode in (item_mode, bitstream_mode):
            if mode not in ("replace", "add"):
                raise ValueError("mode must be 'replace' or 'add'")

        if validate:
            opts = self.get_bulk_access_condition_options(config_name)
            if item_conditions is not None:
                self._validate_conditions(item_conditions, opts.get("itemAccessConditionOptions", []), "item")
            if bitstream_conditions is not None:
                self._validate_conditions(bitstream_conditions,
                                          opts.get("bitstreamAccessConditionOptions", []), "bitstream")

        def norm(conds):
            """
            Normalize the access conditions by formatting the dates.

            Parameters:
                conds (List[Dict[str, Any]]): The list of access conditions to normalize.

            Returns:
                List[Dict[str, Any]]: The normalized list of access conditions.
            """
            out = []
            for c in conds:
                d = {"name": c["name"]}
                if c.get("startDate"):
                    d["startDate"] = _fmt_date(c["startDate"])
                if c.get("endDate"):
                    d["endDate"] = _fmt_date(c["endDate"])
                out.append(d)
            return out

        payload: Dict[str, Any] = {}
        if item_conditions is not None:
            payload["item"] = {"mode": item_mode, "accessConditions": norm(item_conditions)}
        if bitstream_conditions is not None:
            bs: Dict[str, Any] = {"mode": bitstream_mode, "accessConditions": norm(bitstream_conditions)}
            if bitstream_uuids:
                bs["constraints"] = {"uuid": list(bitstream_uuids)}
            payload["bitstream"] = bs
        return payload

    def run_bulk_access_control(self, target_uuids: Sequence[str], payload: Dict[str, Any],
                                filename: str = "bulk-access.json") -> Dict[str, Any]:
        """
        Start the bulk-access-control script for items / collections /
        communities (a collection or community applies to all items inside).
        Returns the created process; use wait_for_process() to follow it.

        Parameters:
            target_uuids (Sequence[str]): The list of target UUIDs for the bulk-access-control script.
            payload (Dict[str, Any]): The payload containing access conditions and modes.
            filename (str): The name of the JSON file to use for the bulk-access-control script.

        Returns:
            Dict[str, Any]: The created process for the bulk-access-control script.
        """
        if not target_uuids:
            raise ValueError("target_uuids must not be empty")
        parameters = [{"name": "-u", "value": u} for u in target_uuids]
        parameters.append({"name": "-f", "value": filename})
        files = {
            "properties": (None, json.dumps(parameters), "application/json"),
            "file": (filename, json.dumps(payload).encode("utf-8"), "application/json"),
        }
        url = self._url(SCRIPTS, "/bulk-access-control/processes")
        logger.info("Starting bulk-access-control on %d target(s): %s", len(target_uuids), payload)
        process = self._request("POST", url, "Start bulk-access-control", expected=(200, 201, 202),
                                headers=self._headers(content_type=None), files=files).json()
        logger.info("bulk-access-control process %s: %s", process.get("processId"), process.get("processStatus"))
        return process

    def get_process(self, process_id: int) -> Dict[str, Any]:
        """
        Get the details of a specific process by its ID.

        Parameters:
            process_id (int): The ID of the process to retrieve.

        Returns:
            Dict[str, Any]: The details of the requested process.
        """
        url = self._url(PROCESSES, f"/{process_id}")
        return self._request("GET", url, f"Get process {process_id}").json()

    def wait_for_process(self, process_id: int, timeout: float = 600, poll_interval: float = 3) -> Dict[str, Any]:
        """Poll until the process is COMPLETED or FAILED (raises DSpaceError on FAILED/timeout).

        Parameters:
            process_id (int): The ID of the process to wait for.
            timeout (float): The maximum time to wait for the process to complete, in seconds.
            poll_interval (float): The interval between polling attempts, in seconds.

        Returns:
            Dict[str, Any]: The details of the completed process.

        Raises:
            DSpaceError: If the process fails or the timeout is reached.
        """
        deadline = time.monotonic() + timeout
        while True:
            proc = self.get_process(process_id)
            status = proc.get("processStatus")
            if status == "COMPLETED":
                return proc
            if status == "FAILED":
                raise DSpaceError(f"Process {process_id} failed; see /system/processes/{process_id}/output")
            if time.monotonic() > deadline:
                raise DSpaceError(f"Process {process_id} still {status} after {timeout}s")
            time.sleep(poll_interval)
