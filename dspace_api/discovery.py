# -*- coding: UTF-8 -*-
"""
Discovery (search, facets) and browse for DSpace API (DSpace 7+ / DSpace-CRIS)

    GET /discover/search                       search configuration: filters, operators, sort options
    GET /discover/search/objects               search (query, dsoType, scope, configuration, f.<filter>, sort)
    GET /discover/search/facets                facets with their top values for a query
    GET /discover/facets                       facet names of a configuration
    GET /discover/facets/{name}                values of one facet (paged; prefix, sort=count|index)
    GET /discover/browses[/{index}]            browse indexes
    GET /discover/browses/search/byFields      browse index configured for metadata fields
    GET /discover/browses/{index}/entries      1st level of a metadata browse (authors, subjects, ...)
    GET /discover/browses/{index}/items        items of a flat browse, or of one entry

    d = client.discovery
    for item in d.iter_search("dc.title:climate*", dso_type="item", scope=coll,
                              filters={"author": "Doe, Jane", "dateIssued": ("[2020 TO 2026]", "equals")}):
        ...
    d.facet_counts("subject", scope=coll)                 # {"Climate": 12, ...} - all values, paged
    for entry in d.iter_browse_entries("author"):        # every author with item counts
        items = list(d.iter_browse_items("author", value=entry["value"]))

Filters use the contract syntax f.<name>=<value>,<operator>; names and
operators come from get_search_config() (operators: equals, notequals,
authority, notauthority, contains, notcontains, query).

The methods further down (search_objects, find_by_*, get_all_results, ...)
are the original simple helpers and keep their behaviour.
"""

import logging
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from ._base import BaseManager
from .config import Endpoints, with_all_languages
from .exceptions import handle_response_error
from .projection import EmbedArg, ProjectionArg

# Logger for the module
logger = logging.getLogger(__name__)

# Discovery API endpoints and related constants
DISCOVER = "/discover"
OPERATORS = ("equals", "notequals", "authority", "notauthority", "contains", "notcontains", "query")
DSO_TYPES = ("all", "item", "community", "collection")

# {"author": "Doe"} / {"author": ("Doe", "contains")} / [("author", "Doe", "authority"), ...]
FilterArg = Union[None, Dict[str, Any], Sequence[Tuple[str, ...]]]


def filter_params(filters: FilterArg) -> List[Tuple[str, str]]:
    """
    Discovery filters as query parameters f.<name>=<value>,<operator>.

        {"author": "Doe, Jane"}                       -> f.author=Doe, Jane,equals
        {"subject": ["a", "b"]}                       -> two f.subject parameters (AND)
        {"title": ("rain", "contains")}               -> f.title=rain,contains
        [("author", "1234-uuid", "authority")]        -> f.author=1234-uuid,authority
        {"dateIssued": "[2020 TO 2026],equals"}       -> passed through (operator already given)

    Parameters:
        filters (FilterArg): The filters to convert into query parameters.

    Returns:
        List[Tuple[str, str]]: A list of query parameter tuples.
    """
    if not filters:
        return []
    items: List[Tuple[str, Any]] = list(filters.items()) if isinstance(filters, dict) else \
        [(f[0], f[1:] if len(f) > 2 else f[1]) for f in filters]
    params = []
    for name, value in items:
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, tuple):
                text, operator = v[0], (v[1] if len(v) > 1 else "equals")
            else:
                text, operator = v, None
                head, _, tail = str(v).rpartition(",")
                if head and tail in OPERATORS:
                    text, operator = head, tail
            operator = operator or "equals"
            if operator not in OPERATORS:
                raise ValueError(f"Unknown filter operator {operator!r}; use one of {OPERATORS}")
            params.append((f"f.{name}", f"{text},{operator}"))
    return params


class DiscoveryManager(BaseManager):
    """Manages DSpace discovery search, facets and browse indexes"""

    def __init__(self, auth):
        """Initialize the DiscoveryManager with the given authentication."""
        super().__init__(auth)

    # ==================================================================
    # Search (BaseManager based: retries, paging, projection / embed)
    # ==================================================================

    def _search_params(self, query=None, dso_type=None, scope=None, configuration=None, filters: FilterArg = None,
                       sort=None) -> List[Tuple[str, Any]]:
        """Convert search parameters into query parameters.

        Parameters:
            query (Optional[str]): The search query.
            dso_type (Optional[str]): The type of DSpace object (e.g., "item", "collection").
            scope (Optional[str]): The scope of the search.
            configuration (Optional[str]): The search configuration to use.
            filters (FilterArg, optional): The filters to apply. Defaults to None.
            sort (Optional[str]): The sort order.

        Returns:
            List[Tuple[str, Any]]: A list of query parameter tuples.
        """
        if dso_type and str(dso_type).lower() not in DSO_TYPES:
            raise ValueError(f"dso_type must be one of {DSO_TYPES}")
        params = [(k, v) for k, v in (("query", query), ("dsoType", dso_type and str(dso_type).lower()),
                                      ("scope", scope), ("configuration", configuration), ("sort", sort)) if v]
        return params + filter_params(filters)

    def get_search_config(self, scope: Optional[str] = None, configuration: Optional[str] = None) -> Dict[str, Any]:
        """
        Get the discovery search configuration.

        Parameters:
            scope (Optional[str], optional): The scope of the search. Defaults to None.
            configuration (Optional[str], optional): The search configuration to use. Defaults to None.

        Returns:
            Dict[str, Any]: The search configuration, including filters and sort options.

        Example response:
        {"filters": [{"filter", "type", "hasFacets", "operators"}], "sortOptions": [...], ...}"""
        return self._get_json(self._url(DISCOVER, "/search"), "Get discovery search configuration",
                              params=self._search_params(scope=scope, configuration=configuration))

    def get_search_filters(self, scope: Optional[str] = None, configuration: Optional[str] = None) -> List[str]:
        """Filter names usable in filters={...}.

        Parameters:
            scope (Optional[str], optional): The scope of the search. Defaults to None.
            configuration (Optional[str], optional): The search configuration to use. Defaults to None.

        Returns:
            List[str]: A list of filter names.
        """
        return [f.get("filter") for f in self.get_search_config(scope, configuration).get("filters") or []]

    def get_sort_options(self, scope: Optional[str] = None, configuration: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get the available sort options for the given scope and configuration.

        Parameters:
            scope (Optional[str], optional): The scope of the search. Defaults to None.
            configuration (Optional[str], optional): The search configuration to use. Defaults to None.

        Returns:
            List[Dict[str, Any]]: A list of sort option data.
        """
        return self.get_search_config(scope, configuration).get("sortOptions") or []

    def search(self, query: Optional[str] = None, dso_type: Optional[str] = None, scope: Optional[str] = None,
               configuration: Optional[str] = None, filters: FilterArg = None, sort: Optional[str] = None,
               page: int = 0, size: int = 20, all_languages: Optional[bool] = None,
               projection: ProjectionArg = None, embed: EmbedArg = None) -> Dict[str, Any]:
        """One page of /discover/search/objects (raw response, incl. facets).

        Parameters:
            query (Optional[str], optional): The search query. Defaults to None.
            dso_type (Optional[str], optional): The type of DSpace object (e.g., "item", "collection"). Defaults to None.
            scope (Optional[str], optional): The scope of the search. Defaults to None.
            configuration (Optional[str], optional): The search configuration to use. Defaults to None.
            filters (FilterArg, optional): The filters to apply. Defaults to None.
            sort (Optional[str], optional): The sort order. Defaults to None.
            page (int, optional): The page number to retrieve. Defaults to 0.
            size (int, optional): The number of results per page. Defaults to 20.
            all_languages (Optional[bool], optional): Whether to include all languages. Defaults to None.
            projection (ProjectionArg, optional): Projection to apply. Defaults to None.
            embed (EmbedArg, optional): Embed options. Defaults to None.

        Returns:
            Dict[str, Any]: The raw search response.
        """
        params = self._search_params(query, dso_type, scope, configuration, filters, sort)
        params += [("page", page), ("size", size)]
        return self._get_json(self._url(DISCOVER, "/search/objects"), "Discovery search", params=params,
                              all_languages=all_languages, projection=projection, embed=embed)

    @staticmethod
    def search_result(response: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Get the search result from a /discover/search/objects response.

        Parameters:
            response (Dict[str, Any]): The raw search response.

        Returns:
            Tuple[List[Dict[str, Any]], Dict[str, Any]]: A tuple containing the list of objects and the page information.

        (objects, page) of a /discover/search/objects response."""
        result = (response.get("_embedded") or {}).get("searchResult") or {}
        return (result.get("_embedded") or {}).get("objects") or [], result.get("page") or {}

    def iter_search(self, query: Optional[str] = None, dso_type: Optional[str] = None, scope: Optional[str] = None,
                    configuration: Optional[str] = None, filters: FilterArg = None, sort: Optional[str] = None,
                    page_size: int = 100, max_results: Optional[int] = None, with_hits: bool = False,
                    all_languages: Optional[bool] = None, projection: ProjectionArg = None,
                    embed: EmbedArg = None) -> Iterator[Dict[str, Any]]:
        """
        Every matching object across all pages (a generator). Yields the
        indexableObject; with_hits=True yields the search result wrappers
        (indexableObject in _embedded, hitHighlights) instead.

        Parameters:
            query (Optional[str], optional): Search query. Defaults to None.
            dso_type (Optional[str], optional): The type of DSpace object to search for. Defaults to None.
            scope (Optional[str], optional): The scope of the search. Defaults to None.
            configuration (Optional[str], optional): The search configuration to use. Defaults to None.
            filters (FilterArg, optional): Filters to apply. Defaults to None.
            sort (Optional[str], optional): Sort order. Defaults to None.
            page_size (int, optional): Number of results per page. Defaults to 100.
            max_results (Optional[int], optional): Maximum number of results to yield. Defaults to None.
            with_hits (bool, optional): Whether to yield search result wrappers instead of indexable objects. Defaults to False.
            all_languages (Optional[bool], optional): Whether to include all languages. Defaults to None.
            projection (ProjectionArg, optional): Projection to apply. Defaults to None.
            embed (EmbedArg, optional): Embed options. Defaults to None.

        Yields:
            Dict[str, Any]: Each matching object or search result wrapper, depending on with_hits.
        """
        page, yielded, previous = 0, 0, None
        while True:
            response = self.search(query, dso_type, scope, configuration, filters, sort, page, page_size,
                                   all_languages, projection, embed)
            objects, info = self.search_result(response)
            if objects and objects == previous:
                logger.warning("Discovery returned the same page twice - stopping (server ignores paging?)")
                return
            previous = objects
            for obj in objects:
                found = obj if with_hits else (obj.get("_embedded") or {}).get("indexableObject")
                if not found:
                    continue
                yield found
                yielded += 1
                if max_results is not None and yielded >= max_results:
                    return
            total_pages = info.get("totalPages")
            if not objects or (total_pages is not None and page + 1 >= total_pages) or \
                    (total_pages is None and len(objects) < page_size):
                return
            page += 1

    def count(self, query: Optional[str] = None, dso_type: Optional[str] = None, scope: Optional[str] = None,
              configuration: Optional[str] = None, filters: FilterArg = None) -> int:
        """Returns the total number of matches for the given query and filters.

        Parameters:
            query (Optional[str], optional): Search query. Defaults to None.
            dso_type (Optional[str], optional): The type of DSpace object to search for. Defaults to None.
            scope (Optional[str], optional): The scope of the search. Defaults to None.
            configuration (Optional[str], optional): The search configuration to use. Defaults to None.
            filters (FilterArg, optional): Filters to apply. Defaults to None.

        Returns:
            int: The total number of matches for the given query and filters.
        """
        _, info = self.search_result(self.search(query, dso_type, scope, configuration, filters, size=1))
        return int(info.get("totalElements") or 0)

    # ------------------------------------------------------------------
    # Facets
    # ------------------------------------------------------------------

    def search_facets(self, query: Optional[str] = None, dso_type: Optional[str] = None,
                      scope: Optional[str] = None, configuration: Optional[str] = None,
                      filters: FilterArg = None, names: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        """
        Facets for a query, each with its first page of values in
        facet["_embedded"]["values"] ([{"label", "count", "authorityKey"?}]).
        names: keep only these facets.

        Parameters:
            query (Optional[str], optional): Search query. Defaults to None.
            dso_type (Optional[str], optional): The type of DSpace object to search for. Defaults to None.
            scope (Optional[str], optional): The scope of the search. Defaults to None.
            configuration (Optional[str], optional): The search configuration to use. Defaults to None.
            filters (FilterArg, optional): Filters to apply. Defaults to None.
            names (Optional[Iterable[str]], optional): Facet names to keep. Defaults to None.

        Returns:
            List[Dict[str, Any]]: The list of facets with their first page of values.
        """
        params = self._search_params(query, dso_type, scope, configuration, filters)
        data = self._get_json(self._url(DISCOVER, "/search/facets"), "Discovery facets", params=params)
        facets = (data.get("_embedded") or {}).get("facets") or []
        wanted = set(names) if names else None
        return [f for f in facets if wanted is None or f.get("name") in wanted]

    def list_facets(self, scope: Optional[str] = None, configuration: Optional[str] = None) -> List[Dict[str, Any]]:
        """Facets configured for a scope / configuration (name, facetType, facetLimit, ...), without values.
        
        Parameters:
            scope (Optional[str], optional): The scope of the search. Defaults to None.
            configuration (Optional[str], optional): The search configuration to use. Defaults to None.

        Returns:
            List[Dict[str, Any]]: The list of configured facets without values.
        """
        data = self._get_json(self._url(DISCOVER, "/facets"), "List facets",
                              params=self._search_params(scope=scope, configuration=configuration))
        return (data.get("_embedded") or {}).get("facets") or []

    def get_facet(self, name: str, query: Optional[str] = None, scope: Optional[str] = None,
                  configuration: Optional[str] = None, filters: FilterArg = None, dso_type: Optional[str] = None,
                  prefix: Optional[str] = None, sort: Optional[str] = None, page: int = 0,
                  size: int = 50) -> Dict[str, Any]:
        """
        One page of a facet's values. sort: "count" (default: most frequent
        first) or "index" (alphabetical), optionally ",asc|desc".
        prefix: only values starting with it (DSpace extension used by the UI).

        Parameters:
            name (str): The name of the facet.
            query (Optional[str], optional): Search query. Defaults to None.
            scope (Optional[str], optional): The scope of the search. Defaults to None.
            configuration (Optional[str], optional): The search configuration to use. Defaults to None.
            filters (FilterArg, optional): Filters to apply. Defaults to None.
            dso_type (Optional[str], optional): The type of DSpace object to search for. Defaults to None.
            prefix (Optional[str], optional): Only values starting with this prefix. Defaults to None.
            sort (Optional[str], optional): Sort order, "count" or "index", optionally with ",asc|desc". Defaults to None.
            page (int, optional): The page number to retrieve. Defaults to 0.
            size (int, optional): The number of values per page. Defaults to 50.

        Returns:
            Dict[str, Any]: The facet with its values for the specified page.
        """
        params = self._search_params(query, dso_type, scope, configuration, filters, sort)
        if prefix:
            params.append(("prefix", prefix))
        params += [("page", page), ("size", size)]
        return self._get_json(self._url(DISCOVER, f"/facets/{name}"), f"Get facet {name}", params=params)

    def iter_facet_values(self, name: str, page_size: int = 100, max_results: Optional[int] = None,
                          **kwargs) -> Iterator[Dict[str, Any]]:
        """
        All values of a facet ({"label", "count", "authorityKey"?, ...}).
        Facet pages carry no totalPages - paging follows hasMore / the next link.
        kwargs: query, scope, configuration, filters, dso_type, prefix, sort.

        Parameters:
            name (str): The name of the facet.
            page_size (int, optional): The number of values per page. Defaults to 100.
            max_results (Optional[int], optional): The maximum number of results to yield. Defaults to None.
            **kwargs: Additional arguments passed to `get_facet` (query, scope, configuration, filters, dso_type, prefix, sort).

        Yields:
            Dict[str, Any]: Each value of the facet.
        """
        page, yielded, previous = 0, 0, None
        while True:
            data = self.get_facet(name, page=page, size=page_size, **kwargs)
            values = (data.get("_embedded") or {}).get("values") or []
            if values and values == previous:
                logger.warning("Facet %s returned the same page twice - stopping", name)
                return
            previous = values
            for value in values:
                yield value
                yielded += 1
                if max_results is not None and yielded >= max_results:
                    return
            has_next = data.get("hasMore") or "next" in (data.get("_links") or {})
            if not values or not has_next:
                return
            page += 1

    def facet_counts(self, name: str, **kwargs) -> Dict[str, int]:
        """Facet counts for a given facet.
        
        {label: count} for every value of a facet.

        Parameters:
            name (str): The name of the facet.
            **kwargs: Additional arguments passed to `iter_facet_values` (query, scope, configuration, filters, dso_type, prefix, sort).

        Returns:
            Dict[str, int]: A dictionary mapping facet labels to their counts.
        """
        return {v.get("label"): int(v.get("count") or 0) for v in self.iter_facet_values(name, **kwargs)}

    # ------------------------------------------------------------------
    # Browse
    # ------------------------------------------------------------------

    def get_browse_indexes(self) -> List[Dict[str, Any]]:
        """
        Get the list of browse indexes.

        Returns:
            List[Dict[str, Any]]: A list of browse indexes, each represented as a dictionary.

        [{"id": "author", "browseType": "valueList"|"flatBrowse"|"hierarchicalBrowse", ...}]"""
        return list(self._iter_embedded(self._url(DISCOVER, "/browses"), "browses", "List browse indexes"))

    def get_browse_index(self, index: str) -> Dict[str, Any]:
        """
        Get the configuration of a specific browse index by its ID.

        Parameters:
            index (str): The ID of the browse index.

        Returns:
            Dict[str, Any]: The browse index configuration.
        """
        return self._get_json(self._url(DISCOVER, f"/browses/{index}"), f"Get browse index {index}")

    def find_browse_index(self, fields: Union[str, Iterable[str]]) -> Optional[Dict[str, Any]]:
        """
        Browse index configured for metadata field(s), e.g. "dc.contributor.author"; None if there is none.

        Parameters:
            fields (Union[str, Iterable[str]]): The metadata field(s) to search for.

        Returns:
            Optional[Dict[str, Any]]: The browse index configuration if found, otherwise None.
        """
        fields = [fields] if isinstance(fields, str) else list(fields)
        if not fields:
            raise ValueError("At least one metadata field is required")
        return self._get_optional(self._url(DISCOVER, "/browses/search/byFields"), "Find browse index by fields",
                                  params=[("fields", f) for f in fields])

    def _browse_page(self, url: str, key: str, operation: str, params: List[Tuple[str, Any]],
                     starts_with: Optional[str], page_size: int, max_results: Optional[int],
                     all_languages: Optional[bool] = None, embed: EmbedArg = None) -> Iterator[Dict[str, Any]]:
        """
        Internal helper to iterate over browse pages.

        Parameters:
            url (str): The URL to fetch the browse page from.
            key (str): The key to extract from the embedded response.
            operation (str): The operation description for logging or error messages.
            params (List[Tuple[str, Any]]): The query parameters for the request.
            starts_with (Optional[str]): The prefix to start browsing from.
            page_size (int): The number of items per page.
            max_results (Optional[int]): The maximum number of results to yield.
            all_languages (Optional[bool]): Whether to include all languages.
            embed (EmbedArg): The embed argument for the request.

        Yields:
            Dict[str, Any]: Each browse entry.
        """
        if starts_with:
            # DSpace answers 422 when page and startsWith are combined: one page from the prefix on
            size = max_results or page_size
            data = self._get_json(url, operation, params=params + [("startsWith", starts_with), ("size", size)],
                                  all_languages=all_languages, embed=embed)
            yield from ((data.get("_embedded") or {}).get(key) or [])[:size]
            return
        yield from self._iter_embedded(url, key, operation, params, page_size=page_size, max_results=max_results,
                                       all_languages=all_languages, embed=embed)

    def iter_browse_entries(self, index: str, scope: Optional[str] = None, sort: Optional[str] = None,
                            starts_with: Optional[str] = None, page_size: int = 100,
                            max_results: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """
        1st level of a metadata browse ("valueList"): {"value", "authority", "count", ...}.
        starts_with: jump to the entries starting at this prefix (one page).
        sort: "default,asc" / "default,desc".

        Parameters:
            index (str): The ID of the browse index.
            scope (Optional[str]): The scope of the browse.
            sort (Optional[str]): The sort order, e.g., "default,asc" or "default,desc".
            starts_with (Optional[str]): The prefix to start browsing from.
            page_size (int): The number of items per page.
            max_results (Optional[int]): The maximum number of results to yield.

        Yields:
            Dict[str, Any]: Each browse entry.
        """
        params = [(k, v) for k, v in (("scope", scope), ("sort", sort)) if v]
        return self._browse_page(self._url(DISCOVER, f"/browses/{index}/entries"), "browseEntries",
                                 f"Browse entries of {index}", params, starts_with, page_size, max_results)

    def iter_browse_items(self, index: str, value: Optional[str] = None, authority: Optional[str] = None,
                          scope: Optional[str] = None, sort: Optional[str] = None,
                          starts_with: Optional[str] = None, page_size: int = 100,
                          max_results: Optional[int] = None, all_languages: Optional[bool] = None,
                          embed: EmbedArg = None) -> Iterator[Dict[str, Any]]:
        """
        Items of a flat browse (e.g. "title", "dateissued"), or - for a
        metadata browse - of one entry, given by value or authority (not both).
        sort: "<sortOption>,asc|desc" from get_browse_index()["sortOptions"].

        Parameters:
            index (str): The ID of the browse index.
            value (Optional[str]): The value to filter by.
            authority (Optional[str]): The authority to filter by.
            scope (Optional[str]): The scope of the browse.
            sort (Optional[str]): The sort order, e.g., "default,asc" or "default,desc".
            starts_with (Optional[str]): The prefix to start browsing from.
            page_size (int): The number of items per page.
            max_results (Optional[int]): The maximum number of results to yield.
            all_languages (Optional[bool]): Whether to include all languages.
            embed (EmbedArg): The embed argument for the request.

        Yields:
            Dict[str, Any]: Each browse item.
        """
        if value is not None and authority is not None:
            raise ValueError("Pass value or authority, not both")
        params = [(k, v) for k, v in (("filterValue", value), ("filterAuthority", authority),
                                      ("scope", scope), ("sort", sort)) if v is not None]
        return self._browse_page(self._url(DISCOVER, f"/browses/{index}/items"), "items", f"Browse items of {index}",
                                 params, starts_with, page_size, max_results, all_languages, embed)

    def iter_entry_items(self, entry: Dict[str, Any], page_size: int = 100, max_results: Optional[int] = None,
                         all_languages: Optional[bool] = None) -> Iterator[Dict[str, Any]]:
        """Items of a browse entry by following its "items" link (as the contract recommends).
        
        Parameters:
            entry (Dict[str, Any]): The browse entry containing the "items" link.
            page_size (int): The number of items per page.
            max_results (Optional[int]): The maximum number of results to yield.
            all_languages (Optional[bool]): Whether to include all languages.

        Yields:
            Dict[str, Any]: Each browse item.
        """
        href = ((entry.get("_links") or {}).get("items") or {}).get("href")
        if not href:
            raise ValueError("Browse entry has no items link")
        return self._iter_embedded(self.resolve_href(href), "items", "Browse entry items",
                                   page_size=page_size, max_results=max_results, all_languages=all_languages)

    # ==================================================================
    # Original helpers
    # ==================================================================

    def get_total_and_objects(self, results):
        """Return (total_elements, objects) from a discovery response with DSpace's nested structure.

        Parameters:
            results (dict): The discovery response.

        Returns:
            Tuple[int, List[dict]]: Total elements and list of objects.
        """
        objects = (results.get('_embedded', {})
                           .get('searchResult', {})
                           .get('_embedded', {})
                           .get('objects', []))
        total_elements = (results.get('_embedded', {})
                                   .get('searchResult', {})
                                   .get('page', {})
                                   .get('totalElements', len(objects)))
        return total_elements, objects


    def search_objects(self, query, page=0, size=10, sort="score,DESC",
                      projection="preventMetadataSecurity", scope=None,
                      filters=None, all_languages=None):
        """
        Perform a discovery search query

        Parameters:
            query: Search query string
            page: Page number (0-based)
            size: Number of results per page
            sort: Sort criteria (default: "score,DESC")
            projection: Projection type
            scope: Optional scope UUID (collection, community)
            filters: Optional list of filter queries
            all_languages: add projection=allLanguages (combined with `projection`);
                None = client setting

        Returns:
            dict: Search results
        """
        logger.info("Running discovery query: %s", query)

        url = f"{self.config.dspace_url}{Endpoints.DISCOVER_SEARCH}"

        params = {
            "query": query,
            "sort": sort,
            "page": page,
            "size": size,
            "projection": projection
        }

        if scope:
            params["scope"] = scope

        if filters:
            for i, filter_query in enumerate(filters):
                params[f"f.{i}"] = filter_query

        params = with_all_languages(params, self.config.use_all_languages(all_languages))

        try:
            response = self.session.get(
                url,
                headers=self.config.headers,
                params=params,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Search completed successfully, found results")
                return response.json()
            handle_response_error(response, "Discovery search")

        except Exception as e:
            logger.error(f"Discovery search failed: {e}", exc_info=True)
            raise

    def find_by_metadata(self, field, value, entity_type=None, exact_match=True):
        """
        Search for objects by a specific metadata field

        Parameters:
            field: Metadata field name (e.g., "dc.title", "dc.creator")
            value: Value to search for
            entity_type: Optional entity type filter
            exact_match: Whether to search for exact matches

        Returns:
            dict: Search results
        """
        if exact_match:
            query = f'{field}:"{value}"'
        else:
            query = f'{field}:{value}'

        if entity_type:
            query += f' AND entityType:"{entity_type}"'

        return self.search_objects(query)

    def find_by_title(self, title, entity_type=None, exact_match=True):
        """
        Search for objects by title

        Parameters:
            title: Title to search for
            entity_type: Optional entity type filter
            exact_match: Whether to search for exact matches

        Returns:
            dict: Search results
        """
        return self.find_by_metadata("dc.title", title, entity_type, exact_match)

    def find_by_author(self, author_name, entity_type="Publication", exact_match=False):
        """
        Search for publications by author name

        Parameters:
            author_name: Author name to search for
            entity_type: Entity type (default: "Publication")
            exact_match: Whether to search for exact matches

        Returns:
            dict: Search results
        """
        return self.find_by_metadata("dc.contributor.author", author_name, entity_type, exact_match)

    def find_persons(self, name, exact_match=False):
        """
        Search for Person entities

        Parameters:
            name: Person name to search for
            exact_match: Whether to search for exact matches

        Returns:
            dict: Search results
        """
        # Try both family name and given name fields
        if exact_match:
            query = f'(person.familyName:"{name}" OR person.givenName:"{name}" OR dc.title:"{name}") AND entityType:"Person"'
        else:
            query = f'(person.familyName:{name} OR person.givenName:{name} OR dc.title:{name}) AND entityType:"Person"'

        return self.search_objects(query)

    def find_by_handle(self, handle):
        """
        Find an object by its handle

        Parameters:
            handle: DSpace handle (e.g., "123456789/123")

        Returns:
            dict: Search results
        """
        query = f'handle:"{handle}"'
        return self.search_objects(query, size=1)

    def find_by_uuid(self, uuid):
        """
        Find an object by its UUID

        Parameters:
            uuid: Object UUID

        Returns:
            dict: Search results
        """
        query = f'uuid:"{uuid}"'
        return self.search_objects(query, size=1)

    def find_recent_items(self, entity_type=None, days=30, size=20):
        """
        Find recently added items

        Parameters:
            entity_type: Optional entity type filter
            days: Number of days back to search
            size: Number of results to return

        Returns:
            dict: Search results
        """
        query = "*:*"
        if entity_type:
            query += f' AND entityType:"{entity_type}"'

        # Sort by last modified or date accessioned
        sort = "lastModified,DESC"

        return self.search_objects(query, size=size, sort=sort)

    def search_in_collection(self, collection_uuid, query="*:*", **kwargs):
        """
        Search within a specific collection

        Parameters:
            collection_uuid: UUID of the collection to search in
            query: Search query (default: "*:*" for all items)
            **kwargs: Additional search parameters

        Returns:
            dict: Search results
        """
        return self.search_objects(query, scope=collection_uuid, **kwargs)

    def search_in_community(self, community_uuid, query="*:*", **kwargs):
        """
        Search within a specific community

        Parameters:
            community_uuid: UUID of the community to search in
            query: Search query (default: "*:*" for all items)
            **kwargs: Additional search parameters

        Returns:
            dict: Search results
        """
        return self.search_objects(query, scope=community_uuid, **kwargs)

    def get_facets(self, query="*:*", facet_fields=None, scope=None, configuration=None, filters: FilterArg = None):
        """
        Facets for a query: {"_embedded": {"facets": [...]}} from
        /discover/search/facets, limited to facet_fields (facet names such as
        "author", "subject", "dateIssued") when given. See search_facets().

        Parameters:
            query (str): Search query (default: "*:*" for all items)
            facet_fields (list, optional): List of facet field names to include
            scope (str, optional): Scope for the search (collection or community UUID)
            configuration (str, optional): Search configuration
            filters (FilterArg, optional): Filters to apply

        Returns:
            dict: Faceted search results
        """
        facets = self.search_facets(query=query, scope=scope, configuration=configuration, filters=filters,
                                    names=facet_fields)
        return {"_embedded": {"facets": facets}}

    def parse_search_results(self, results):
        """
        Parse search results into a more convenient format

        Parameters:
            results (dict): Raw search results from DSpace API

        Returns:
            list: List of parsed objects
        """
        objects = results.get("_embedded", {}).get("searchResult", {}).get("_embedded", {}).get("objects", [])
        parsed_objects = []

        for obj in objects:
            embedded = obj.get("_embedded", {})
            item = embedded.get("indexableObject", {})

            parsed = {
                "uuid": item.get("uuid"),
                "name": item.get("name"),
                "handle": item.get("handle"),
                "type": item.get("type"),
                "entityType": item.get("entityType"),
                "lastModified": item.get("lastModified"),
                "metadata": {}
            }

            # Extract common metadata fields
            metadata = item.get("metadata", {})
            common_fields = [
                "dc.title", "dc.creator", "dc.contributor.author", "dc.date.issued",
                "dc.type", "dc.description", "dc.subject", "dc.identifier.uri",
                "person.familyName", "person.givenName", "person.identifier.gnd"
            ]

            for field in common_fields:
                if field in metadata:
                    values = metadata[field]
                    if values:
                        parsed["metadata"][field] = [v.get("value") for v in values if v.get("value")]

            # Add title as separate field for convenience
            if "dc.title" in parsed["metadata"] and parsed["metadata"]["dc.title"]:
                parsed["title"] = parsed["metadata"]["dc.title"][0]

            # Add author/creator as separate field
            author_fields = ["dc.contributor.author", "dc.creator"]
            for field in author_fields:
                if field in parsed["metadata"] and parsed["metadata"][field]:
                    parsed["author"] = parsed["metadata"][field][0]
                    break

            # Add person name for Person entities
            if parsed.get("entityType") == "Person":
                family_name = parsed["metadata"].get("person.familyName", [""])[0]
                given_name = parsed["metadata"].get("person.givenName", [""])[0]
                if family_name or given_name:
                    parsed["person_name"] = f"{family_name}, {given_name}".strip(", ")

            parsed_objects.append(parsed)

        return parsed_objects

    def count_results(self, query):
        """
        Get the total count of results for a query without retrieving all results

        Parameters:
            query (str): The search query to count results for.

        Returns:
            int: Total number of results matching the query.
        """
        results = self.search_objects(query, size=1)
        total, _ = self.get_total_and_objects(results)
        return total


    def get_all_results(self, query: str, max_results: int | None = None, size: int = 20,
                        all_languages: bool | None = None) -> list[dict]:
        """
        Fetch all discovery results across pages.
        - Supports 'page' metadata both at root and under _embedded.searchResult.page
        - Stops before requesting an empty, beyond-last page (so tests expecting 2 calls pass)

        Parameters:
            query (str): The search query to fetch results for.
            max_results (int | None, optional): Maximum number of results to fetch. Defaults to None.
            size (int, optional): Number of results per page. Defaults to 20.
            all_languages (bool | None, optional): Whether to include all languages. Defaults to None.

        Returns:
            list[dict]: List of discovery result objects.
        """
        results: list[dict] = []
        page = 0

        def _extract_page_info(res: dict) -> tuple[int | None, int | None]:
            """
            Extract page number and total pages from the response.

            Parameters:
                res (dict): The raw search response from DSpace API.

            Returns:
                tuple[int | None, int | None]: Current page number and total pages, if available.
            """
            page_block = res.get("page") or res.get("_embedded", {}).get("searchResult", {}).get("page") or {}
            number = page_block.get("number")
            total_pages = page_block.get("totalPages")
            return number, total_pages

        while True:
            res = self.search_objects(query, page=page, size=size, all_languages=all_languages)
            # Use helper to get the list of search objects
            _total, objects = self.get_total_and_objects(res)

            # Unwrap indexableObject and collect
            for obj in (objects or []):
                item = obj.get("_embedded", {}).get("indexableObject", {})
                if item:
                    results.append(item)
                    if max_results is not None and len(results) >= max_results:
                        return results[:max_results]

            # Pagination decision
            number, total_pages = _extract_page_info(res)

            # If we have valid page metadata and we're on the last page, stop without extra fetch
            if number is not None and total_pages is not None and number >= (total_pages - 1):
                break

            # Otherwise, advance to next page; if there were no objects, also stop to avoid a loop
            if not objects:
                break
            page += 1

        return results
