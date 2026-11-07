# -*- coding: UTF-8 -*-
"""
Projections and embeds for DSpace REST requests.

DSpace lets every HAL GET request shape its response (RestContract
projections.md):

    projection=full                     every link embedded, max. two levels
    projection=allLanguages             metadata in every language (DSpace-CRIS)
    embed=bundles                       embed one linked resource
    embed=bundles/bitstreams            nested: the bundles AND their bitstreams
    embed=bundles&embed=owningCollection   several (or comma separated)
    embed.size=bundles=50               page size of an embedded list

Several projections can be combined by repeating the parameter; DSpace
builds a composite projection (Utils.obtainProjection). embed.size is not
described on the contract page but is supported by the server; without it
an embedded list holds only its default page (usually 20) - use
get_embedded_page() / is_embedded_complete() to detect truncation.

The toolkit accepts these per call on its read methods:

    client.items.get_item(uuid, embed=["bundles/bitstreams", "owningCollection"])
    client.items.get_item(uuid, embed={"bundles": 50, "bundles/bitstreams": 200})
    client.collections.get_collection(uuid, projection="full")

and the helpers below read the result:

    get_embedded(item, "owningCollection")          -> dict or None
    get_embedded(item, "bundles/bitstreams")        -> flat list of bitstreams
"""

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

# Standard projection and embed constants and normalization functions for DSpace REST API requests.
FULL = "full"
ALL_LANGUAGES = "allLanguages"

# Accepted argument shapes
EmbedArg = Union[None, str, Iterable[str], Mapping[str, Optional[int]]]
ProjectionArg = Union[None, str, Iterable[str]]


def _split(value) -> List[str]:
    """Split a comma-separated string or iterable into a list of non-empty, stripped strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [v.strip() for v in value if v and str(v).strip()]


def normalize_projection(projection: ProjectionArg) -> List[str]:
    """Normalize a projection argument into a list of unique projection names.

    'full' / 'full,allLanguages' / ['full'] -> ['full', ...] (order kept, no duplicates).
    """
    seen: List[str] = []
    for name in _split(projection):
        if name not in seen:
            seen.append(name)
    return seen


def _clean_path(path: str) -> str:
    """Clean and validate an embed path, ensuring it is non-empty and properly formatted."""
    path = str(path).strip().strip("/")
    if not path or any(not part.strip() for part in path.split("/")):
        raise ValueError(f"Invalid embed path {path!r}")
    return "/".join(part.strip() for part in path.split("/"))


def normalize_embed(embed: EmbedArg) -> Tuple[List[str], Dict[str, int]]:
    """Normalize an embed argument into a list of unique embed paths and a dictionary of embed sizes.

    -> (embed paths, embed sizes)

    embed may be a string ("bundles,owningCollection"), a list of paths, or a
    dict {path: size or None}. A size applies to that embedded list.

    Parameters:
        embed (EmbedArg): The embed argument to normalize.

    Returns:
        Tuple[List[str], Dict[str, int]]: A tuple containing the list of unique embed paths and a dictionary of embed sizes.
    """
    if embed is None:
        return [], {}
    sizes: Dict[str, int] = {}
    if isinstance(embed, Mapping):
        paths = []
        for path, size in embed.items():
            clean = _clean_path(path)
            paths.append(clean)
            if size is not None:
                if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                    raise ValueError(f"embed size for {path!r} must be a positive int, not {size!r}")
                sizes[clean] = size
    else:
        paths = [_clean_path(p) for p in _split(embed)]
    unique: List[str] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique, sizes


def _merge(params, extra: Sequence[Tuple[str, str]]):
    """Add (key, value) pairs to params (None / dict / list of tuples), skipping exact duplicates.
    
    Parameters:
        params (None | dict | list of tuples): The existing parameters to merge into.
        extra (Sequence[Tuple[str, str]]): The additional (key, value) pairs to add.

    Returns:
        The merged parameters in the same shape as the input `params`.
    """
    if not extra:
        return params
    if params is None:
        params = []
    if isinstance(params, Mapping):
        merged = dict(params)
        for key, value in extra:
            current = merged.get(key)
            if current is None:
                merged[key] = value
                continue
            values = list(current) if isinstance(current, (list, tuple)) else [current]
            if value not in values:
                values.append(value)
            merged[key] = values if len(values) > 1 else values[0]
        return merged
    items = list(params)
    for pair in extra:
        if pair not in items:
            items.append(pair)
    return items


def apply_view(params, projection: ProjectionArg = None, embed: EmbedArg = None):
    """
    Return params with projection / embed / embed.size added, in the same
    shape that was passed (None becomes a list of tuples). Existing values
    (e.g. projection=preventMetadataSecurity, embed=group) are kept.

    Parameters:
        params (None | dict | list of tuples): The existing parameters to merge into.
        projection (ProjectionArg, optional): The projection argument to apply.
        embed (EmbedArg, optional): The embed argument to apply.

    Returns:
        The merged parameters with the projection and embed applied, in the same shape as the input `params`.
    """
    paths, sizes = normalize_embed(embed)
    extra = [("projection", p) for p in normalize_projection(projection)]
    extra += [("embed", p) for p in paths]
    extra += [("embed.size", f"{p}={n}") for p, n in sizes.items()]
    return _merge(params, extra)


def view_params(params, config=None, all_languages: Optional[bool] = None,
                projection: ProjectionArg = None, embed: EmbedArg = None):
    """
    params + the caller's projection/embed + allLanguages if the config
    (per-call value > languages() scope > default) asks for it.

    Parameters:
        params (None | dict | list of tuples): The existing parameters to merge into.
        config (optional): The configuration object that may influence the allLanguages setting.
        all_languages (Optional[bool], optional): Whether to include all languages.
        projection (ProjectionArg, optional): The projection argument to apply.
        embed (EmbedArg, optional): The embed argument to apply.

    Returns:
        The merged parameters with the projection and embed applied, including allLanguages if requested.
    """
    resolve = getattr(config, "use_all_languages", None)
    wants_all = resolve(all_languages) if resolve else bool(all_languages)
    projections = normalize_projection(projection)
    if wants_all and ALL_LANGUAGES not in projections:
        projections.append(ALL_LANGUAGES)
    return apply_view(params, projections, embed)


# ---------------------------------------------------------------------------
# Reading embedded results
# ---------------------------------------------------------------------------

def _is_page(value) -> bool:
    """Check if the given value represents a paginated embedded resource.
    
    Parameters:
        value (Any): The value to check.

    Returns:
        bool: True if the value represents a paginated embedded resource, False otherwise.
    """
    return isinstance(value, dict) and "page" in value and isinstance(value.get("_embedded"), dict)


def _page_items(page: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract the list of items from a paginated embedded resource.
    Parameters:
        page (Dict[str, Any]): The paginated embedded resource.

    Returns:
        List[Dict[str, Any]]: The list of items contained in the paginated resource, or an empty list if none are found.
    """
    for value in page["_embedded"].values():
        if isinstance(value, list):
            return value
    return []


def get_embedded_page(resource: Optional[Dict[str, Any]], rel: str
                      ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[int]]:
    """
    Get the embedded page for the given relationship key `rel` from the `resource`.

    (items, totalElements) of an embedded list on `resource`, or (None, None)
    if it was not embedded. totalElements is None when DSpace sent no page info.

    Parameters:
        resource (Optional[Dict[str, Any]]): The resource containing the embedded list.
        rel (str): The relationship key for the embedded list.

    Returns:
        Tuple[Optional[List[Dict[str, Any]]], Optional[int]]: A tuple containing the list of embedded items and the total number of elements, or (None, None) if not embedded.
    """
    value = ((resource or {}).get("_embedded") or {}).get(rel)
    if value is None:
        return None, None
    if isinstance(value, list):
        return value, None
    if _is_page(value):
        return _page_items(value), (value.get("page") or {}).get("totalElements")
    return None, None


def is_embedded_complete(resource: Optional[Dict[str, Any]], rel: str) -> bool:
    """True if the embedded list `rel` holds all its elements (not truncated by paging).
    
    Parameters:
        resource (Optional[Dict[str, Any]]): The resource containing the embedded list.
        rel (str): The relationship key for the embedded list.

    Returns:
        bool: True if the embedded list holds all its elements, False otherwise.
    """
    items, total = get_embedded_page(resource, rel)
    if items is None:
        return False
    return total is None or len(items) >= total


def get_embedded(resource: Optional[Dict[str, Any]], path: str, default=None):
    """
    Follow an embed path through `_embedded`.

    Returns the embedded object for single links (owningCollection), a list
    for list links (bundles), and a flat list when a list appears anywhere on
    the path (bundles/bitstreams -> all bitstreams of all bundles).
    `default` is returned when the first step was not embedded.

    Parameters:
        resource (Optional[Dict[str, Any]]): The resource to traverse.
        path (str): The embed path to follow through `_embedded`.
        default (Any, optional): The value to return if the first step is not embedded.

    Returns:
        The embedded object, a list of embedded items, or the default value if not found.
    """
    nodes: List[Any] = [resource]
    is_list = False
    for step, rel in enumerate(_clean_path(path).split("/")):
        found = []
        for node in nodes:
            value = ((node or {}).get("_embedded") or {}).get(rel)
            if value is None:
                continue
            if isinstance(value, list) or _is_page(value):
                is_list = True
                found.extend(value if isinstance(value, list) else _page_items(value))
            else:
                found.append(value)
        if step == 0 and not found and not is_list:
            return default
        nodes = found
    if is_list:
        return nodes
    return nodes[0] if nodes else default
