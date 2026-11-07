# -*- coding: UTF-8 -*-
"""
Shared request handling for DSpace API managers.

Every manager that talks to the REST API through ``session.request()``
inherits from BaseManager, so they all get the same behaviour:

- the session is always the auth object's 'current' one
  (DSpaceAuth.logout() replaces it)
- CSRF tokens returned by DSpace are stored after every response; retries,
  token refresh, re-login and the CSRF retry live in DSpaceSession
- HAL collections are paged transparently
- links from ``_links.*.href`` can be followed, but only on the
  configured DSpace server (the session carries the auth header)
- GET requests take ``projection=`` and ``embed=`` (see projection.py),
  and get ``projection=allLanguages`` when enabled - per call
  (``all_languages=True/False``), in a ``config.languages()`` block, or
  as the config default (``DSpaceConfig(all_languages=...)`` /
  ``DSPACE_ALL_LANGUAGES``)
"""

import logging
from typing import Any, Dict, Iterator, Optional, Sequence, Tuple

from .projection import EmbedArg, ProjectionArg, view_params
from .exceptions import handle_response_error
from .utils import extract_uuid_from_url, validate_uuid

# Logging setup for the module
logger = logging.getLogger(__name__)

# Constants for DSpace API headers

# Header for the DSpace XSRF token returned in responses
XSRF_RESPONSE_HEADER = "DSPACE-XSRF-TOKEN"


class BaseManager:
    """Base class with request, paging and link helpers"""

    DEFAULT_PAGE_SIZE = 100

    def __init__(self, auth):
        """Store the authentication manager and configuration.

        Parameters:
            auth: The authentication manager instance.
        """
        self.auth = auth
        self.config = auth.config

    @property
    def session(self):
        """Return the current session from the authentication manager.
        
        Returns:
            The current session object.
        """
        return self.auth.session

    # ------------------------------------------------------------------
    # URLs and headers
    # ------------------------------------------------------------------

    def _url(self, *parts: str) -> str:
        """Construct a full URL by joining the base DSpace URL with the given path parts.
        
        Parameters:
            *parts: The path parts to join with the base DSpace URL.

        Returns:
            The full URL as a string.
        """
        return self.config.dspace_url + "".join(str(p) for p in parts)

    # A uri-list body is just the absolute URL of the referenced resource
    _uri_list = _url

    def _headers(self, content_type: Optional[str] = "application/json", accept: str = "application/json"):
        """Construct the headers for a request, including content type and accept headers.
        
        Parameters:
            content_type: The value for the 'Content-Type' header, or None to omit it.
            accept: The value for the 'Accept' header.

        Returns:
            A dictionary of headers for the request.
        """
        headers = {k: v for k, v in self.config.headers.items() if k.lower() not in ("content-type", "accept")}
        headers["Accept"] = accept
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def resolve_href(self, href: str) -> str:
        """Turn a HAL href or an '/api/...' path into an absolute URL on the
        configured server. Absolute URLs on other hosts are refused, because
        the session would send the Authorization header along.

        Parameters:
            href: The HAL href or '/api/...' path to resolve.

        Returns:
            The absolute URL on the configured server.
        """
        if href.startswith(("http://", "https://")):
            if not href.startswith(self.config.dspace_url):
                raise ValueError(f"Refusing to follow {href!r}: not under {self.config.dspace_url}")
            return href
        if href.startswith("/api/") and self.config.dspace_url.endswith("/api"):
            return self.config.dspace_url[: -len("/api")] + href
        return self._url("/" + href.lstrip("/"))

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------

    def _update_token(self, response) -> None:
        """Update the authentication token based on the response, if applicable."""
        update = getattr(self.auth, "_update_token", None)
        if update:
            update(response)

    def _request(self, method: str, url: str, operation: str, expected=(200,),
                 all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None, **kwargs):
        """Perform an HTTP request with the given method, URL, and parameters.
        Handles projection, embedding, and language settings for GET requests.
        
        Parameters:
            method: The HTTP method to use (e.g., "GET", "POST").
            url: The URL to send the request to.
            operation: A description of the operation for logging or error messages.
            expected: A tuple of expected HTTP status codes.
            all_languages: Whether to retrieve all language variants (for GET requests).
            projection: Optional HAL projection parameter (for GET requests).
            embed: Optional HAL embed parameter (for GET requests).
            **kwargs: Additional arguments to pass to the request.
        
        Returns:
            The HTTP response object.
        
        """
        if method.upper() == "GET":
            kwargs["params"] = view_params(kwargs.get("params"), self.config, all_languages, projection, embed)
        elif projection or embed:
            raise ValueError("projection/embed only apply to GET requests")
        kwargs.setdefault("headers", self.config.headers)
        kwargs.setdefault("timeout", self.config.timeout)
        # Retries, token refresh, re-login and the CSRF retry happen in
        # DSpaceSession (see session.py), so every manager gets them.
        response = self.session.request(method, url, **kwargs)
        self._update_token(response)

        if response.status_code not in expected:
            logger.error("%s failed: HTTP %s %s", operation, response.status_code, response.text[:500])
            handle_response_error(response, operation)
        return response

    def _get_json(self, url: str, operation: str, params=None,
                  all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Dict[str, Any]:
        """Perform a GET request and return the JSON response as a dictionary.

        Parameters:
            url: The URL to send the GET request to.
            operation: A description of the operation for logging or error messages.
            params: Optional query parameters as a dictionary.
            all_languages: Whether to retrieve all language variants.
            projection: Optional HAL projection parameter.
            embed: Optional HAL embed parameter.

        Returns:
            The JSON response as a dictionary.
        """
        return self._request("GET", url, operation, params=params, all_languages=all_languages,
                             projection=projection, embed=embed).json()

    def _get_optional(self, url: str, operation: str, params=None,
                      all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Optional[Dict[str, Any]]:
        """Perform a GET request that maps '204 No Content' (and an empty 200) to None.

        Parameters:
            url: The URL to send the GET request to.
            operation: A description of the operation for logging or error messages.
            params: Optional query parameters as a dictionary.
            all_languages: Whether to retrieve all language variants.
            projection: Optional HAL projection parameter.
            embed: Optional HAL embed parameter.

        Returns:
            The JSON response as a dictionary, or None if there is no content.
        """
        response = self._request("GET", url, operation, expected=(200, 204), params=params,
                                 all_languages=all_languages, projection=projection, embed=embed)
        if response.status_code == 204 or not response.content:
            return None
        return response.json() or None

    @staticmethod
    def _json_or_none(response) -> Optional[Dict[str, Any]]:
        """Return the JSON content of the response, or None if there is no content.

        Parameters:
            response: The HTTP response object.

        Returns:
            The JSON content as a dictionary, or None if there is no content.
        """
        return response.json() if response.content else None

    def _iter_embedded(self, url: str, key, operation: str,
                       params: Optional[Sequence[Tuple[str, Any]]] = None,
                       page_size: Optional[int] = None,
                       max_results: Optional[int] = None,
                       all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Iterator[Dict[str, Any]]:
        """
        Yield every object of a paginated HAL collection (_embedded.<key>).
        key may be a tuple of alternative names (first one present wins).
        params is a list of tuples so repeated keys (embed=a&embed=b) work.

        Parameters:
            url: The URL of the HAL collection.
            key: The key or tuple of alternative keys to look for in the _embedded section.
            operation: A description of the operation for logging or error messages.
            params: Optional query parameters as a list of key-value pairs.
            page_size: The number of results per page.
            max_results: The maximum number of results to yield.
            all_languages: Whether to retrieve all language variants.
            projection: Optional HAL projection parameter.
            embed: Optional HAL embed parameter.

        Yields:
            Each object in the paginated HAL collection.
        """
        keys = (key,) if isinstance(key, str) else tuple(key)
        size = page_size or self.DEFAULT_PAGE_SIZE
        page = 0
        yielded = 0
        while True:
            p = list(params or []) + [("page", page), ("size", size)]
            data = self._request("GET", url, operation, params=p, all_languages=all_languages,
                                 projection=projection, embed=embed).json()
            embedded = data.get("_embedded", {})
            objects = next((embedded[k] for k in keys if k in embedded), [])
            for obj in objects:
                yield obj
                yielded += 1
                if max_results is not None and yielded >= max_results:
                    return
            total_pages = (data.get("page") or {}).get("totalPages")
            if not objects:
                break
            if total_pages is not None and page + 1 >= total_pages:
                break
            if total_pages is None and len(objects) < size:
                break
            page += 1

    def _discover(self, query: str = "", dso_type: Optional[str] = None, configuration: Optional[str] = None,
                  scope: Optional[str] = None, sort: Optional[str] = None, size: int = 20,
                  max_results: Optional[int] = None, all_languages: Optional[bool] = None,
                  projection: ProjectionArg = None, embed: EmbedArg = None):
        """Perform a discovery search returning the indexableObjects (all pages up to max_results).
        
        Parameters:
            query: The search query string.
            dso_type: The type of DSpace object to search for.
            configuration: The discovery configuration to use.
            scope: The scope of the search.
            sort: The sort order of the results.
            size: The number of results per page.
            max_results: The maximum number of results to return.
            all_languages: Whether to retrieve all language variants.
            projection: Optional HAL projection parameter.
            embed: Optional HAL embed parameter.

        Returns:
            A list of indexable DSpace objects matching the search criteria.
        """
        url = self._url("/discover/search/objects")
        page = 0
        found = []
        while True:
            params = [("page", page), ("size", size)]
            for key, value in (("query", query.strip() if query else None), ("dsoType", dso_type),
                               ("configuration", configuration), ("scope", scope), ("sort", sort)):
                if value:
                    params.append((key, value))
            result = (self._get_json(url, "Discovery search", params, all_languages=all_languages,
                                     projection=projection, embed=embed)
                      .get("_embedded", {}).get("searchResult", {}))
            objects = result.get("_embedded", {}).get("objects", [])
            for obj in objects:
                dso = obj.get("_embedded", {}).get("indexableObject")
                if dso:
                    found.append(dso)
                    if max_results is not None and len(found) >= max_results:
                        return found
            page += 1
            if not objects or page >= (result.get("page") or {}).get("totalPages", 0):
                return found

    def get_by_href(self, href: str, all_languages: Optional[bool] = None, projection: ProjectionArg = None, embed: EmbedArg = None) -> Dict[str, Any]:
        """Follow a HAL link, e.g. item['_links']['owningCollection']['href'].

        Parameters:
            href: The HAL link to follow.
            all_languages: Whether to retrieve all language variants.
            projection: Optional HAL projection parameter.
            embed: Optional HAL embed parameter.

        Returns:
            The JSON response as a dictionary.
        """
        return self._get_json(self.resolve_href(href), f"GET {href}", all_languages=all_languages,
                              projection=projection, embed=embed)

    # ------------------------------------------------------------------
    # References
    # ------------------------------------------------------------------

    @staticmethod
    def _uuid_of(ref) -> Optional[str]:
        """
        UUID from a DSpace object dict, a UUID string or a HAL href.
        Returns None when ref is something else (e.g. a name or an email).

        Parameters:
            ref: The reference to extract the UUID from. Can be a DSpace object dict, a UUID string, or a HAL href.

        Returns:
            The UUID string if it can be determined, otherwise None.
        """
        if isinstance(ref, dict):
            return ref.get("uuid") or ref.get("id")
        if isinstance(ref, str):
            if validate_uuid(ref):
                return ref
            if ref.startswith(("http://", "https://", "/")):
                return extract_uuid_from_url(ref)
        return None

    @staticmethod
    def _format_metadata_field(values):
        """Normalise a str / dict / list into a DSpace MetadataValue list.

        Parameters:
            values: The metadata value(s) to normalise.

        Returns:
            A list of DSpace MetadataValue dictionaries.
        """
        if isinstance(values, str):
            return [{"value": values}]
        if isinstance(values, dict):
            return [values]
        if isinstance(values, list):
            return [v if isinstance(v, dict) else {"value": str(v)} for v in values]
        return [{"value": str(values)}]

    def _format_metadata(self, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Format the metadata dictionary into the structure expected by DSpace.

        Parameters:
            metadata: The metadata dictionary to format.

        Returns:
            The formatted metadata dictionary.
        """
        return {field: self._format_metadata_field(v) for field, v in (metadata or {}).items()}

    def _metadata_patch_ops(self, operation: str, metadata: Dict[str, Any]):
        """Generate JSON Patch operations for modifying metadata.

        Parameters:
            operation: The JSON Patch operation ('add', 'replace', or 'remove').
            metadata: The metadata dictionary to modify.

        Returns:
            A list of JSON Patch operations for the given metadata modification.
        """
        if operation not in ("add", "replace", "remove"):
            raise ValueError("operation must be 'add', 'replace' or 'remove'")
        ops = []
        for field, values in metadata.items():
            op = {"op": operation, "path": f"/metadata/{field}"}
            if operation != "remove":
                op["value"] = self._format_metadata_field(values)
            ops.append(op)
        return ops

    @staticmethod
    def _upsert_ops(existing: Dict[str, Any], metadata: Dict[str, Any], formatter):
        """Generate JSON Patch operations to upsert metadata fields.

        For fields that already exist in the object, use 'replace'.
        For new fields, use 'add'.

        Parameters:
            existing: The existing metadata dictionary of the object.
            metadata: The new metadata to upsert.
            formatter: A function to format the metadata values.

        Returns:
            A list of JSON Patch operations for upserting the metadata.
        """
        return [{"op": "replace" if existing.get(f) else "add", "path": f"/metadata/{f}", "value": formatter(v)}
                for f, v in metadata.items()]
