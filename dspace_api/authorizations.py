# -*- coding: UTF-8 -*-
"""
Authorization checks for DSpace API (DSpace 7+ / DSpace-CRIS): "may this
user do X on object Y?" - the same checks the Angular UI uses to show or
hide buttons, so scripts can test permissions before acting.

    GET /authz/authorizations/{id}[/eperson|/feature|/object]
    GET /authz/authorizations/search/object?uri=&eperson=&feature=
    GET /authz/authorizations/search/objects?uuid=..&type=&eperson=&feature=..   (batch)
    GET /authz/features[/{id}]                         (admin only)
    GET /authz/features/search/resourcetype?type=      (admin only)

    auth = client.authorizations
    auth.can(item, Feature.CAN_EDIT_METADATA)                      # current user
    auth.can(collection, Feature.ADMINISTRATOR_OF, eperson=jane)   # someone else (admin, or jane herself)
    auth.features_on(item)                                         # {"canEditMetadata", "withdrawItem", ...}
    auth.check(item, [Feature.WITHDRAW_ITEM, Feature.CAN_DELETE])  # {"withdrawItem": True, "canDelete": False}
    auth.require(item, Feature.CAN_MOVE)                           # raises AuthorizationError if not
    auth.can_many(items, Feature.CAN_EDIT_METADATA)                # {uuid: bool}, one request per type
    auth.is_site_admin()

Objects can be DSpace object dicts (with _links.self or type + uuid), REST
URLs, or (type, id) tuples such as ("item", uuid) or ("workspaceitem", 42).

Without eperson the server evaluates the logged-in user (anonymous when not
logged in); asking about another eperson needs admin rights.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from ._base import BaseManager
from .exceptions import AuthorizationError, DSpaceError
from .metadata import DSO_PATHS
from .projection import EmbedArg
from .utils import validate_uuid

# Logger for this module
logger = logging.getLogger(__name__)

# API endpoints for authorizations and features
AUTHORIZATIONS = "/authz/authorizations"
FEATURES = "/authz/features"

# object type -> REST collection path (DSO_PATHS plus non-DSpace-object resources)
TYPE_PATHS = dict(DSO_PATHS, **{
    "workspaceitem": "/submission/workspaceitems",
    "workflowitem": "/workflow/workflowitems",
    "version": "/versioning/versions",
    "versionhistory": "/versioning/versionhistories",
    "resourcepolicy": "/authz/resourcepolicies",
    "process": "/system/processes",
})

# Type aliases for object and eperson references
ObjectRef = Union[str, Dict[str, Any], Tuple[str, Any]]
EPersonRef = Union[None, str, Dict[str, Any]]


class Feature:
    """
    Feature ids known from DSpace 7.6 - 9 (Angular FeatureID). Which ones a
    server offers depends on version and configuration; list_features()
    (admin) shows the real set.
    """
    ADMINISTRATOR_OF = "administratorOf"
    IS_COLLECTION_ADMIN = "isCollectionAdmin"      # on the site: admin of at least one collection
    IS_COMMUNITY_ADMIN = "isCommunityAdmin"        # on the site: admin of at least one community
    LOGIN_ON_BEHALF_OF = "loginOnBehalfOf"
    CAN_CHANGE_PASSWORD = "canChangePassword"
    CAN_MANAGE_GROUP = "canManageGroup"
    CAN_MANAGE_GROUPS = "canManageGroups"
    CAN_MANAGE_POLICIES = "canManagePolicies"
    CAN_MANAGE_MAPPINGS = "canManageMappings"
    CAN_MANAGE_RELATIONSHIPS = "canManageRelationships"
    CAN_MANAGE_BITSTREAM_BUNDLES = "canManageBitstreamBundles"
    CAN_MANAGE_VERSIONS = "canManageVersions"
    CAN_CREATE_VERSION = "canCreateVersion"
    CAN_EDIT_VERSION = "canEditVersion"
    CAN_DELETE_VERSION = "canDeleteVersion"
    CAN_EDIT_ITEM = "canEditItem"
    CAN_EDIT_METADATA = "canEditMetadata"
    CAN_DELETE = "canDelete"
    CAN_MOVE = "canMove"
    CAN_MAKE_PRIVATE = "canMakePrivate"
    WITHDRAW_ITEM = "withdrawItem"
    REINSTATE_ITEM = "reinstateItem"
    CAN_REORDER_BITSTREAMS = "canReorderBitstreams"
    CAN_CREATE_BITSTREAM = "canCreateBitstream"
    CAN_CREATE_BUNDLE = "canCreateBundle"
    CAN_DOWNLOAD = "canDownload"
    CAN_REQUEST_A_COPY = "canRequestACopy"
    CAN_REGISTER_DOI = "canRegisterDOI"
    CAN_SUBSCRIBE = "canSubscribeDso"
    CAN_VIEW_USAGE_STATISTICS = "canViewUsageStatistics"
    CAN_SEND_FEEDBACK = "canSendFeedback"
    CAN_CLAIM_ITEM = "canClaimItem"
    SHOW_CLAIM_ITEM = "showClaimItem"
    CAN_SYNCHRONIZE_WITH_ORCID = "canSynchronizeWithORCID"
    EPERSON_REGISTRATION = "epersonRegistration"
    EPERSON_FORGOT_PASSWORD = "epersonForgotPassword"


def parse_authorization_id(authorization_id: str) -> Dict[str, Optional[str]]:
    """
    Parse an authorization ID into its components.

    '[eperson-uuid_]feature_category.model_object-id' ->
    {"eperson": uuid|None, "feature": ..., "object_type": "core.item", "object_id": ...}
    
    Parameters:
        authorization_id (str): The authorization ID string to parse.

    Returns:
        Dict[str, Optional[str]]: A dictionary with keys "eperson", "feature", "object_type", and "object_id".
    """
    parts = str(authorization_id).split("_")
    eperson = parts[0] if len(parts) >= 4 and validate_uuid(parts[0]) else None
    rest = parts[1:] if eperson else parts
    if len(rest) < 3:
        raise ValueError(f"Not an authorization id: {authorization_id!r}")
    return {"eperson": eperson, "feature": rest[0], "object_type": rest[1], "object_id": "_".join(rest[2:])}


def feature_of(authorization: Dict[str, Any]) -> Optional[str]:
    """Feature id of an authorization (embedded feature, else parsed from the id).
    
    Parameters:
        authorization (Dict[str, Any]): The authorization object to extract the feature from.

    Returns:
        Optional[str]: The feature ID if available, None otherwise.
    """
    embedded = (authorization.get("_embedded") or {}).get("feature") or {}
    if embedded.get("id"):
        return embedded["id"]
    try:
        return parse_authorization_id(authorization.get("id", ""))["feature"]
    except ValueError:
        return None


class AuthorizationManager(BaseManager):
    """Read-only permission checks via /authz/authorizations and /authz/features"""

    # ------------------------------------------------------------------
    # Object references
    # ------------------------------------------------------------------

    def object_uri(self, obj: ObjectRef) -> str:
        """Absolute REST URL of the object an authorization check is about.
        
        Parameters:
            obj (ObjectRef): The object reference to build the URI for.

        Returns:
            str: The absolute REST URL of the object.
        """
        if isinstance(obj, tuple):
            kind, ident = obj
            if kind not in TYPE_PATHS:
                raise ValueError(f"Unknown object type {kind!r}; known: {sorted(TYPE_PATHS)}")
            return self._url(TYPE_PATHS[kind], f"/{ident}")
        if isinstance(obj, dict):
            href = ((obj.get("_links") or {}).get("self") or {}).get("href")
            if href:
                return self.resolve_href(href)
            ident = obj.get("uuid") or obj.get("id")
            if obj.get("type") and ident is not None:
                return self.object_uri((obj["type"], ident))
            raise ValueError("Object dict needs _links.self.href or type + uuid/id")
        if isinstance(obj, str) and obj.startswith(("http://", "https://", "/")):
            return self.resolve_href(obj)
        raise ValueError(f"Cannot build an object URI from {obj!r} - pass a dict, URL or (type, id)")

    def _eperson_param(self, eperson: EPersonRef) -> Optional[str]:
        """Get the UUID of an eperson reference.

        Parameters:
            eperson (EPersonRef): The eperson reference to extract the UUID from.

        Returns:
            Optional[str]: The UUID of the eperson if available, None otherwise.
        """
        if eperson is None:
            return None
        uuid = self._uuid_of(eperson)
        if not uuid:
            raise ValueError(f"eperson must be a UUID or eperson dict, not {eperson!r}")
        return uuid

    # ------------------------------------------------------------------
    # Authorizations
    # ------------------------------------------------------------------

    def get_authorization(self, authorization_id: str, embed: EmbedArg = None) -> Dict[str, Any]:
        """Get an authorization by its ID.

        Parameters:
            authorization_id (str): The ID of the authorization to retrieve.
            embed (EmbedArg, optional): Embed related resources. Defaults to None.

        Returns:
            Dict[str, Any]: The authorization object.
        """
        return self._get_json(self._url(AUTHORIZATIONS, f"/{authorization_id}"),
                              f"Get authorization {authorization_id}", embed=embed)

    def search_object(self, obj: ObjectRef, feature: Optional[str] = None, eperson: EPersonRef = None,
                      embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """All authorizations (optionally one feature) the user has on one object.

        Parameters:
            obj (ObjectRef): The object reference to check authorizations on.
            feature (str, optional): The feature to filter authorizations by. Defaults to None.
            eperson (EPersonRef, optional): The eperson reference to check authorizations for. Defaults to None.
            embed (EmbedArg, optional): Embed related resources. Defaults to None.

        Returns:
            List[Dict[str, Any]]: A list of authorization objects.
        """
        params = [("uri", self.object_uri(obj))]
        if feature:
            params.append(("feature", feature))
        eperson_uuid = self._eperson_param(eperson)
        if eperson_uuid:
            params.append(("eperson", eperson_uuid))
        url = self._url(AUTHORIZATIONS, "/search/object")
        return list(self._iter_embedded(url, "authorizations", "Search authorizations", params, embed=embed))

    def features_on(self, obj: ObjectRef, eperson: EPersonRef = None) -> Set[str]:
        """Every feature id the user has on the object (one request).

        Parameters:
            obj (ObjectRef): The object reference to check features on.
            eperson (EPersonRef, optional): The eperson reference to check features for. Defaults to None.

        Returns:
            Set[str]: A set of feature IDs the user has on the object.
        """
        return {f for f in (feature_of(a) for a in self.search_object(obj, eperson=eperson, embed="feature")) if f}

    def can(self, obj: ObjectRef, feature: str, eperson: EPersonRef = None) -> bool:
        """Check if the user has a specific feature on an object.

        Parameters:
            obj (ObjectRef): The object reference to check the feature on.
            feature (str): The feature to check.
            eperson (EPersonRef, optional): The eperson reference to check the feature for. Defaults to None.

        Returns:
            bool: True if the feature is granted, False otherwise.
        """
        return bool(self.search_object(obj, feature=feature, eperson=eperson))

    def check(self, obj: ObjectRef, features: Iterable[str], eperson: EPersonRef = None) -> Dict[str, bool]:
        """{feature: bool} for several features on one object, in one request.

        Parameters:
            obj (ObjectRef): The object reference to check features on.
            features (Iterable[str]): The features to check.
            eperson (EPersonRef, optional): The eperson reference to check features for. Defaults to None.

        Returns:
            Dict[str, bool]: A dictionary mapping feature IDs to their granted status.
        """
        granted = self.features_on(obj, eperson)
        return {feature: feature in granted for feature in features}

    def require(self, obj: ObjectRef, feature: str, eperson: EPersonRef = None) -> None:
        """Raise AuthorizationError unless the feature is granted - a pre-flight check for scripts.

        Parameters:
            obj (ObjectRef): The object reference to check the feature on.
            feature (str): The feature to check.
            eperson (EPersonRef, optional): The eperson reference to check the feature for. Defaults to None.

        Raises:
            AuthorizationError: If the feature is not granted.
        """
        if not self.can(obj, feature, eperson):
            who = f"eperson {self._eperson_param(eperson)}" if eperson else "the current user"
            raise AuthorizationError(f"{who} lacks {feature!r} on {self.object_uri(obj)}", status_code=403)

    # ------------------------------------------------------------------
    # Batch checks
    # ------------------------------------------------------------------

    @staticmethod
    def _rest_type(path: str) -> str:
        """
        Convert a REST API path to the type parameter used in search/objects.

        Parameters:
            path (str): The REST API path.

        Returns:
            str: The corresponding type parameter.

        Example:
            '/core/items' -> 'core.items' (the type parameter of search/objects)."""
        return ".".join(path.strip("/").split("/"))

    def _group_by_type(self, objects: Iterable[ObjectRef]) -> Dict[str, List[Tuple[str, ObjectRef]]]:
        """
        Group objects by their REST API path type.

        Parameters:
            objects (Iterable[ObjectRef]): The objects to group.

        Returns:
            Dict[str, List[Tuple[str, ObjectRef]]]: A dictionary mapping path types to lists of (identifier, object) tuples.
        """
        grouped: Dict[str, List[Tuple[str, ObjectRef]]] = {}
        for obj in objects:
            uri = self.object_uri(obj)
            path, _, ident = uri[len(self.config.dspace_url):].rpartition("/")
            grouped.setdefault(path, []).append((ident, obj))
        return grouped

    def features_on_many(self, objects: Sequence[ObjectRef], features: Union[str, Iterable[str], None] = None,
                         eperson: EPersonRef = None, batch_size: int = 50) -> Dict[str, Set[str]]:
        """
        Get granted features for many objects using search/objects.

        Parameters:
            objects (Sequence[ObjectRef]): The objects to check features on.
            features (Union[str, Iterable[str], None], optional): The features to check. Defaults to None.
            eperson (EPersonRef, optional): The eperson reference to check features for. Defaults to None.
            batch_size (int, optional): The batch size for search/objects requests. Defaults to 50.

        Returns:
            Dict[str, Set[str]]: A dictionary mapping object IDs to sets of granted features.

        Notes:
            {object id: granted features} for many objects with search/objects
            (DSpace 7.6+; one request per type and batch). Without `features`
            (search/objects returns nothing then), on servers without that
            search, and when it fails server-side, it falls back to one
            search/object request per object.
        """
        wanted = [features] if isinstance(features, str) else list(features or [])
        eperson_uuid = self._eperson_param(eperson)
        result: Dict[str, Set[str]] = {}
        url = self._url(AUTHORIZATIONS, "/search/objects")
        for path, entries in self._group_by_type(objects).items():
            for ident, _ in entries:
                result[ident] = set()
            if not wanted:
                # search/objects needs feature names - it answers an empty page without them
                for ident, obj in entries:
                    result[ident] = self.features_on(obj, eperson)
                continue
            for start in range(0, len(entries), batch_size):
                batch = entries[start:start + batch_size]
                params = [("type", self._rest_type(path))] + [("uuid", ident) for ident, _ in batch]
                params += [("feature", f) for f in wanted]
                if eperson_uuid:
                    params.append(("eperson", eperson_uuid))
                try:
                    found = list(self._iter_embedded(url, "authorizations", "Batch authorization search", params,
                                                     embed="feature"))
                except DSpaceError as exc:
                    status = getattr(exc, "status_code", None)
                    if status not in (400, 404, 405, 422) and not (status or 0) >= 500:
                        raise
                    logger.warning("search/objects failed (HTTP %s: %s), checking one by one", status,
                                   (exc.response_text or "")[:200])
                    for ident, obj in batch:
                        granted = self.features_on(obj, eperson)
                        result[ident] = {f for f in granted if not wanted or f in wanted}
                    continue
                for authorization in found:
                    try:
                        ident = parse_authorization_id(authorization.get("id", ""))["object_id"]
                    except ValueError:
                        continue
                    feature = feature_of(authorization)
                    if ident in result and feature and (not wanted or feature in wanted):
                        result[ident].add(feature)
        return result

    def can_many(self, objects: Sequence[ObjectRef], feature: str, eperson: EPersonRef = None) -> Dict[str, bool]:
        """
        Get a dictionary mapping object IDs to a boolean indicating if the feature is granted.

        Parameters:
            objects (Sequence[ObjectRef]): The objects to check the feature on.
            feature (str): The feature to check.
            eperson (EPersonRef, optional): The eperson reference to check the feature for. Defaults to None.

        Returns:
            Dict[str, bool]: A dictionary mapping object IDs to a boolean indicating if the feature is granted.
        """
        return {ident: feature in granted
                for ident, granted in self.features_on_many(objects, feature, eperson).items()}

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def site_uri(self) -> str:
        """
        Get the URI of the site object.

        Returns:
            str: The URI of the site object.

        Raises:
            ValueError: If no site object is found.
        """
        from .site import SiteManager
        site = SiteManager(self.auth).get_site(all_languages=False)
        if not site:
            raise ValueError("No Site object found")
        return self._url(DSO_PATHS["site"], f"/{site['uuid']}")

    def is_site_admin(self, eperson: EPersonRef = None) -> bool:
        """
        Check if the eperson is a site administrator.

        Parameters:
            eperson (EPersonRef, optional): The eperson reference to check. Defaults to None.

        Returns:
            bool: True if the eperson is a site administrator, False otherwise.
        """
        return self.can(self.site_uri(), Feature.ADMINISTRATOR_OF, eperson)

    def is_admin_of(self, obj: ObjectRef, eperson: EPersonRef = None) -> bool:
        """Admin of this community/collection/item (directly, inherited, or site admin)."""
        return self.can(obj, Feature.ADMINISTRATOR_OF, eperson)

    def admin_scope(self, eperson: EPersonRef = None) -> Dict[str, bool]:
        """
        Get the administrative scope of the eperson.

        Parameters:
            eperson (EPersonRef, optional): The eperson reference to check the admin scope for. Defaults to None.

        Returns:
            Dict[str, bool]: A dictionary indicating which admin menus apply.
                Keys are "site", "any_community", and "any_collection".
        """
        granted = self.features_on(self.site_uri(), eperson)
        return {"site": Feature.ADMINISTRATOR_OF in granted,
                "any_community": Feature.IS_COMMUNITY_ADMIN in granted,
                "any_collection": Feature.IS_COLLECTION_ADMIN in granted}

    # ------------------------------------------------------------------
    # Features (admin only)
    # ------------------------------------------------------------------

    def list_features(self) -> List[Dict[str, Any]]:
        """
        List all authorization features.

        Returns:
            List[Dict[str, Any]]: A list of all authorization features.
        """
        return list(self._iter_embedded(self._url(FEATURES), "features", "List authorization features"))

    def get_feature(self, feature_id: str) -> Dict[str, Any]:
        """
        Get details of a specific authorization feature.

        Parameters:
            feature_id (str): The ID of the feature to retrieve.

        Returns:
            Dict[str, Any]: The details of the specified feature.
        """
        return self._get_json(self._url(FEATURES, f"/{feature_id}"), f"Get feature {feature_id}")

    def features_for_type(self, resource_type: str) -> List[Dict[str, Any]]:
        """
        Get features that apply to a specific resource type.

        Parameters:
            resource_type (str): The resource type to get features for, e.g. 'core.item', 'core.collection', 'core.site'.

        Returns:
            List[Dict[str, Any]]: A list of features applicable to the specified resource type.
        """
        url = self._url(FEATURES, "/search/resourcetype")
        return list(self._iter_embedded(url, "features", f"Features for {resource_type}", [("type", resource_type)]))
