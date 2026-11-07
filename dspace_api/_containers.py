# -*- coding: UTF-8 -*-
"""
Features shared by communities and collections ("containers"): logo,
parent community, metadata PATCH and the *Authorized search methods.

    GET    /core/{communities|collections}/{uuid}/logo          204 if none
    POST   /core/{communities|collections}/{uuid}/logo          multipart, only when there is no logo
    DELETE /core/bitstreams/{logo uuid}                          removes the logo (both types)
    GET    /core/{communities|collections}/{uuid}/parentCommunity   204 for top-level communities
    PATCH  /core/{communities|collections}/{uuid}                metadata (MetadataPatch)
    GET    /core/{communities|collections}/search/findAdminAuthorized?query=
    GET    /core/{communities|collections}/search/findEditAuthorized?query=
"""

import hashlib
import logging
import mimetypes
from typing import Any, Dict, Iterable, List, Optional, Union

from .metadata import MetadataPatch, as_operations
from .projection import EmbedArg

# Logging setup for the module
logger = logging.getLogger(__name__)

# API paths for DSpace containers and bitstreams
BITSTREAMS = "/core/bitstreams"
COMMUNITIES = "/core/communities"

# Timeout for logo upload requests (in seconds)
LOGO_TIMEOUT = 120


class ContainerMixin:
    """Mixin providing shared functionality for community and collection managers.

    Includes methods for handling logos, parent communities, and metadata PATCH operations.
    """

    # Container-specific constants
    _CONTAINER_PATH = ""      # "/core/communities" or "/core/collections"
    _CONTAINER_KEY = ""       # HAL key of search results: "communities" / "collections"

    def _container_url(self, uuid: str, suffix: str = "") -> str:
        """Construct the URL for a container resource, optionally with a suffix.
        
        Parameters:
            uuid: The UUID of the container.
            suffix: Optional suffix to append to the URL.

        Returns:
            The full URL for the container resource as a string.
        """
        return self._url(self._CONTAINER_PATH, f"/{uuid}{suffix}")

    # ------------------------------------------------------------------
    # Logo
    # ------------------------------------------------------------------

    def get_logo(self, uuid: str) -> Optional[Dict[str, Any]]:
        """The logo bitstream, or None.
        
        Parameters:
            uuid: The UUID of the container.

        Returns:
            The logo bitstream as a dictionary, or None if there is no logo.
        """
        return self._get_optional(self._container_url(uuid, "/logo"), f"Get logo of {uuid}")

    def set_logo(self, uuid: str, source: Any, name: Optional[str] = None,
                 mime_type: Optional[str] = None) -> Dict[str, Any]:
        """Upload a logo for the container, replacing any existing one.

        Parameters:
            uuid: The UUID of the container.
            source: The source of the logo (file path, bytes, or file-like object).
            name: Optional name of the file.
            mime_type: Optional MIME type of the file.

        Returns:
            The new logo bitstream as a dictionary.
        """
        from .bundles import BundleManager
        name, content = BundleManager._read_source(source, name)
        mime_type = mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        if self.delete_logo(uuid):
            logger.info("Replaced the existing logo of %s", uuid)
        logo = self._request("POST", self._container_url(uuid, "/logo"), f"Upload logo of {uuid}",
                             expected=(200, 201), headers=self._headers(content_type=None),
                             files={"file": (name, content, mime_type)}, timeout=LOGO_TIMEOUT).json()
        remote_md5 = ((logo.get("checkSum") or {}).get("value") or "").lower()
        if remote_md5 and remote_md5 != hashlib.md5(content).hexdigest():
            logger.warning("Logo of %s: server MD5 %s differs from the local file", uuid, remote_md5)
        return logo

    def delete_logo(self, uuid: str) -> bool:
        """Remove the logo of the container.

        Parameters:
            uuid: The UUID of the container.

        Returns:
            True if a logo was deleted, False if there was none.
        """
        logo = self.get_logo(uuid)
        if not logo:
            return False
        self._request("DELETE", self._url(BITSTREAMS, f"/{logo['uuid']}"), f"Delete logo of {uuid}",
                      expected=(204,))
        return True

    # ------------------------------------------------------------------
    # Hierarchy
    # ------------------------------------------------------------------

    def get_parent_community(self, uuid: str, embed: EmbedArg = None) -> Optional[Dict[str, Any]]:
        """Retrieve the (first) parent community of the container.

        Parameters:
            uuid: The UUID of the container.
            embed: Optional HAL embed parameter.

        Returns:
            The parent community as a dictionary, or None for top-level communities.
        """
        return self._get_optional(self._container_url(uuid, "/parentCommunity"), f"Get parent community of {uuid}",
                                  embed=embed)

    def get_ancestors(self, uuid: str, max_depth: int = 50) -> List[Dict[str, Any]]:
        """Retrieve the ancestor communities of the container, from the top-level one down to the direct parent.

        Parameters:
            uuid: The UUID of the container.
            max_depth: Maximum number of ancestor levels to retrieve.

        Returns:
            A list of ancestor communities as dictionaries, ordered from top-level to direct parent.
        """
        trail: List[Dict[str, Any]] = []
        parent = self._get_optional(self._container_url(uuid, "/parentCommunity"), f"Get parent of {uuid}")
        while parent and len(trail) < max_depth:
            trail.insert(0, parent)
            parent = self._get_optional(self._url(COMMUNITIES, f"/{parent['uuid']}/parentCommunity"),
                                        f"Get parent of {parent['uuid']}")
        return trail

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def patch_metadata(self, uuid: str, patch: Union[MetadataPatch, Iterable[Dict[str, Any]]]) -> Dict[str, Any]:
        """Apply a MetadataPatch (or raw operations) to the container's metadata.

        Parameters:
            uuid: The UUID of the container.
            patch: A MetadataPatch object or an iterable of raw JSON Patch operations.

        Returns:
            The updated container as a dictionary.
        """
        ops = as_operations(patch)
        if not ops:
            raise ValueError("Empty metadata patch")
        return self._request("PATCH", self._container_url(uuid), f"Patch metadata of {uuid}",
                             headers=self._headers(), json=ops).json()

    # ------------------------------------------------------------------
    # Authorized searches
    # ------------------------------------------------------------------

    def _authorized_search(self, method: str, query: Optional[str], extra=(), max_results: Optional[int] = None,
                           embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Perform an authorized search for containers based on the given method and query.

        Parameters:
            method: The search method to use (e.g., 'findAdminAuthorized').
            query: Optional search query string.
            extra: Additional query parameters as a list of key-value pairs.
            max_results: Maximum number of results to return.
            embed: Optional HAL embed parameter.

        Returns:
            A list of containers matching the search criteria.
        """
        params = list(extra) + ([("query", query)] if query else [])
        url = self._url(self._CONTAINER_PATH, f"/search/{method}")
        return list(self._iter_embedded(url, self._CONTAINER_KEY, method, params, max_results=max_results,
                                        embed=embed))

    def find_admin_authorized(self, query: Optional[str] = None, max_results: Optional[int] = None,
                              embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Retrieve containers that the current user administers.

        Parameters:
            query: Optional search query string.
            max_results: Maximum number of results to return.
            embed: Optional HAL embed parameter.

        Returns:
            A list of containers administered by the current user.
        """
        return self._authorized_search("findAdminAuthorized", query, max_results=max_results, embed=embed)

    def find_edit_authorized(self, query: Optional[str] = None, max_results: Optional[int] = None,
                             embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Retrieve containers that the current user may edit (WRITE).

        Parameters:
            query: Optional search query string.
            max_results: Maximum number of results to return.
            embed: Optional HAL embed parameter.

        Returns:
            A list of containers the current user may edit.
        """
        return self._authorized_search("findEditAuthorized", query, max_results=max_results, embed=embed)
