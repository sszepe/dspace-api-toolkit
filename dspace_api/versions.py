# -*- coding: UTF-8 -*-
"""
Item versioning for DSpace API (DSpace 7+ / DSpace-CRIS)

    POST  /versioning/versions[?summary=]            text/uri-list: the item -> new version (draft)
    GET   /versioning/versions/{id}[/item|/versionhistory]
    PATCH /versioning/versions/{id}                  /summary only
    GET   /versioning/versionhistories/{id}[/versions|/draftVersion]
    GET   /core/items/{uuid}/version                 204 when the item is not versioned

Creating a version copies the item into a new workspace item (the draft);
it becomes the latest version once deposited (and approved, if the
collection has a workflow). new_version() does all of that:

    v = client.versions
    result = v.new_version(item_uuid, "Fixed author order",
                           metadata={"dc.contributor.author": ["Roe, Richard", "Doe, Jane"]})
    result["item_uuid"]        # the new version's item
    v.list_item_versions(item_uuid)   # newest first

Deleting a version means deleting its item (archived) or its draft workspace
item - discard_draft() does the latter. Version histories may require admin
rights (versioning.item.history.view.admin), and entity items can only be
versioned when versioning.block.entity = false.
"""

import logging
from typing import Any, Dict, List, Optional

from ._base import BaseManager
from .exceptions import DSpaceError
from .projection import EmbedArg

# Logger for the module
logger = logging.getLogger(__name__)

# API endpoints for item versioning and version histories
VERSIONS = "/versioning/versions"
VERSION_HISTORIES = "/versioning/versionhistories"
ITEMS = "/core/items"


class VersionManager(BaseManager):
    """Item versions and version histories"""

    def __init__(self, auth):
        """Initialize the VersionManager with authentication."""
        super().__init__(auth)
        self._workspace = None

    @property
    def workspace(self):
        """Get the workspace manager, initializing it if necessary."""
        if self._workspace is None:
            from .workspaces import WorkspaceManager
            self._workspace = WorkspaceManager(self.auth)
        return self._workspace

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    def get_version(self, version_id, embed: EmbedArg = None) -> Dict[str, Any]:
        """Get the details of a specific version.

        Parameters:
            version_id: The ID of the version
            embed: Optional embedding argument

        Returns:
            Dict[str, Any]: The version details
        """
        return self._get_json(self._url(VERSIONS, f"/{version_id}"), f"Get version {version_id}", embed=embed)

    def get_item_version(self, item_uuid: str, embed: EmbedArg = None) -> Optional[Dict[str, Any]]:
        """Get the version an item represents, or None if the item is not versioned.

        Parameters:
            item_uuid: The UUID of the item
            embed: Optional embedding argument

        Returns:
            Optional[Dict[str, Any]]: The version details or None if not versioned
        """
        return self._get_optional(self._url(ITEMS, f"/{item_uuid}/version"), f"Get version of item {item_uuid}",
                                  embed=embed)

    def get_version_item(self, version_id, all_languages: Optional[bool] = None) -> Dict[str, Any]:
        """Get the item corresponding to a specific version.

        Parameters:
            version_id: The ID of the version
            all_languages: Optional flag to retrieve all language variants

        Returns:
            Dict[str, Any]: The item details
        """
        return self._get_json(self._url(VERSIONS, f"/{version_id}/item"), f"Get item of version {version_id}",
                              all_languages=all_languages)

    def create_version(self, item_uuid: str, summary: Optional[str] = None) -> Dict[str, Any]:
        """Create a new version of an archived item.

        New version of an archived item (admins, or the submitter if
        versioning.submitterCanCreateNewVersion). The new item starts as a
        workspace item - see new_version() for the complete flow.
        422: a draft version already exists in this history.

        Parameters:
            item_uuid: The UUID of the item
            summary: Optional summary for the new version

        Returns:
            Dict[str, Any]: The created version details
        """
        params = {"summary": summary} if summary else None
        version = self._request("POST", self._url(VERSIONS), f"Create version of item {item_uuid}",
                                expected=(200, 201), headers=self._headers("text/uri-list"), params=params,
                                data=self._url(ITEMS, f"/{item_uuid}")).json()
        logger.info("Created version %s (no. %s) of item %s", version.get("id"), version.get("version"), item_uuid)
        return version

    def set_summary(self, version_id, summary: Optional[str]) -> Dict[str, Any]:
        """Set, replace or (summary=None) remove the version summary.

        Parameters:
            version_id: The ID of the version
            summary: The new summary, or None to remove it

        Returns:
            Dict[str, Any]: The updated version details
        """
        op = {"op": "remove", "path": "/summary"} if summary is None else \
            {"op": "add", "path": "/summary", "value": summary}
        return self._request("PATCH", self._url(VERSIONS, f"/{version_id}"), f"Update summary of version {version_id}",
                             headers=self._headers(), json=[op]).json()

    # ------------------------------------------------------------------
    # Histories
    # ------------------------------------------------------------------

    def get_history(self, history_id) -> Dict[str, Any]:
        """Get the details of a specific version history.

        Parameters:
            history_id: The ID of the version history

        Returns:
            Dict[str, Any]: The version history details
        """
        return self._get_json(self._url(VERSION_HISTORIES, f"/{history_id}"), f"Get version history {history_id}")

    def get_version_history(self, version_id) -> Dict[str, Any]:
        """Get the version history of a specific version.

        Parameters:
            version_id: The ID of the version

        Returns:
            Dict[str, Any]: The version history details
        """
        return self._get_json(self._url(VERSIONS, f"/{version_id}/versionhistory"),
                              f"Get history of version {version_id}")

    def get_history_versions(self, history_id, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Get all versions of a specific version history, newest first (a draft is not included).

        Parameters:
            history_id: The ID of the version history
            embed: Optional embedding argument

        Returns:
            List[Dict[str, Any]]: The list of versions in the history
        """
        url = self._url(VERSION_HISTORIES, f"/{history_id}/versions")
        return list(self._iter_embedded(url, "versions", f"List versions of history {history_id}", embed=embed))

    def get_draft_version(self, history_id) -> Optional[Dict[str, Any]]:
        """Get the draft version of a specific version history, or None if there is no draft.

        Parameters:
            history_id: The ID of the version history

        Returns:
            Optional[Dict[str, Any]]: The draft version details or None if no draft exists
        """
        return self._get_optional(self._url(VERSION_HISTORIES, f"/{history_id}/draftVersion"),
                                  f"Get draft version of history {history_id}")

    def history_of_item(self, item_uuid: str) -> Optional[Dict[str, Any]]:
        """Get the version history of the item corresponding to the given UUID.

        Parameters:
            item_uuid: The UUID of the item

        Returns:
            Optional[Dict[str, Any]]: The version history details or None if the item is not versioned
        """
        version = self.get_item_version(item_uuid)
        return self.get_version_history(version["id"]) if version else None

    def list_item_versions(self, item_uuid: str, embed: EmbedArg = "item") -> List[Dict[str, Any]]:
        """All versions in the item's history, newest first ([] if not versioned).
        Parameters:
            item_uuid: The UUID of the item
            embed: Optional embedding argument

        Returns:
            List[Dict[str, Any]]: The list of versions in the item's history
        """
        history = self.history_of_item(item_uuid)
        return self.get_history_versions(history["id"], embed=embed) if history else []

    def latest_version(self, item_uuid: str) -> Optional[Dict[str, Any]]:
        """Get the latest version of the item, or None if not versioned.

        Parameters:
            item_uuid: The UUID of the item

        Returns:
            Optional[Dict[str, Any]]: The latest version details or None if not versioned
        """
        versions = self.list_item_versions(item_uuid)
        return versions[0] if versions else None

    def is_latest(self, item_uuid: str) -> bool:
        """Check if the item is the newest archived version (or not versioned at all).

        Parameters:
            item_uuid: The UUID of the item

        Returns:
            bool: True if the item is the newest archived version or not versioned, False otherwise
        """
        own = self.get_item_version(item_uuid)
        if not own:
            return True
        versions = self.get_history_versions(self.get_version_history(own["id"])["id"])
        return not versions or str(versions[0].get("id")) == str(own.get("id"))

    # ------------------------------------------------------------------
    # Complete flows
    # ------------------------------------------------------------------

    def new_version(self, item_uuid: str, summary: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
                    deposit: bool = True, strict_metadata: bool = True) -> Dict[str, Any]:
        """
        Create a version, optionally change its metadata (routed to the
        submission forms like WorkspaceManager.fill_metadata) and deposit it.

        Returns {"version", "item_uuid", "workspace_item", "workflow_item"}.
        On failure after the version was created the draft is kept, so it
        can be inspected or removed with discard_draft().

        Parameters:
            item_uuid: The UUID of the item
            summary: Optional summary for the new version
            metadata: Optional metadata to update in the new version
            deposit: Whether to deposit the new version immediately (default is True)
            strict_metadata: Whether to enforce strict metadata validation (default is True)

        Returns:
            Dict[str, Any]: Details of the newly created version, including the version, item UUID, workspace item, and workflow item
        """
        version = self.create_version(item_uuid, summary)
        new_item = self.get_version_item(version["id"])
        ws = self.workspace.find_by_item(new_item["uuid"])
        if ws is None:
            raise DSpaceError(f"Version {version.get('id')} has no workspace item (already in workflow?)")
        if metadata:
            ws = self.workspace.fill_metadata(ws["id"], metadata, strict=strict_metadata)
        workflow_item = self.workspace.deposit(ws["id"]) if deposit else None
        return {"version": version, "item_uuid": new_item["uuid"], "workspace_item": ws,
                "workflow_item": workflow_item}

    def discard_draft(self, item_uuid: str) -> bool:
        """
        Discard the draft version of an item, if it exists and is still a workspace item.

        Parameters:
            item_uuid: The UUID of the item

        Returns:
            bool: True if the draft was successfully discarded, False if there was no draft
        """
        history = self.history_of_item(item_uuid)
        draft = self.get_draft_version(history["id"]) if history else None
        if not draft:
            return False
        if draft.get("type") != "workspaceitem":
            raise DSpaceError(f"The draft version is a {draft.get('type')} - only workspace items can be discarded")
        return self.workspace.delete(draft["id"])
