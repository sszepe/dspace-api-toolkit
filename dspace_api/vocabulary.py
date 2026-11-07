# -*- coding: UTF-8 -*-
"""
Controlled vocabularies for DSpace API (DSpace 7+ / DSpace-CRIS)

    GET /submission/vocabularies[/{name}]
    GET /submission/vocabularies/{name}/entries?filter=&exact=&entryID=
    GET /submission/vocabularies/search/byMetadataAndCollection?metadata=&collection=
    GET /submission/vocabularyEntryDetails/{vocabulary}:{id}[/parent|/children]
    GET /submission/vocabularyEntryDetails/search/top?vocabulary=

Vocabularies are value-pair lists from submission-forms.xml (common_types,
common_iso_languages), hierarchical XML vocabularies (srsc) and authorities
(SolrAuthorAuthority, ORCID, CRIS item authorities ...).

    v = client.vocabulary
    v.lookup("common_types", "Article")                     # exact entry or None
    v.check_values("common_types", ["Article", "Artikel"])  # {"Article": entry, "Artikel": None}
    v.vocabulary_for_field("dc.type", collection_uuid)      # which vocabulary a form field uses
    for depth, entry in v.iter_tree("srsc", max_depth=1):   # hierarchical vocabularies
        ...
    md = {"dc.subject": [v.to_metadata_value(entry)]}       # value + authority + confidence

Everything here is readable anonymously (the server may restrict authorities).
"""

import logging
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ._base import BaseManager
from .config import Endpoints

# Logger for the module
logger = logging.getLogger(__name__)

# Endpoints for vocabularies and vocabulary entries
VOCABULARIES = Endpoints.VOCABULARIES                  # /submission/vocabularies
ENTRY_DETAILS = Endpoints.VOCABULARY_ENTRIES           # /submission/vocabularyEntryDetails

# DSpace authority confidence for a value accepted from the authority (Choices.CF_ACCEPTED)
CONFIDENCE_ACCEPTED = 600


def detail_id(vocabulary: str, entry_id: str) -> str:
    """Id of an entry detail: ("srsc", "SCB110") -> "srsc:SCB110" (already prefixed ids are kept).
    
    Parameters:
        vocabulary: The vocabulary name
        entry_id: The entry ID

    Returns:
        str: The detail ID in the form "vocabulary:entry_id"
    """
    entry_id = str(entry_id)
    return entry_id if entry_id.startswith(f"{vocabulary}:") else f"{vocabulary}:{entry_id}"


def has_children(entry: Dict[str, Any]) -> Optional[bool]:
    """otherInformation.hasChildren as bool, None if the server does not say.

    Parameters:
        entry: The vocabulary entry

    Returns:
        Optional[bool]: True if the entry has children, False if not, None if unknown
    """
    value = (entry.get("otherInformation") or {}).get("hasChildren")
    return None if value is None else str(value).lower() == "true"


class VocabularyManager(BaseManager):
    """Controlled vocabularies, authorities and hierarchical vocabulary trees"""

    def __init__(self, auth):
        """Initialize the vocabulary manager with authentication."""
        super().__init__(auth)
        self._lookup_cache: Dict[Tuple[str, str, bool], Optional[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Vocabularies
    # ------------------------------------------------------------------

    def get_vocabularies(self) -> List[Dict[str, Any]]:
        """List of vocabularies with their properties.

        Returns:
            List[Dict[str, Any]]: Each dictionary contains keys "id", "name", "scrollable", "hierarchical", "preloadLevel"
        """
        return list(self._iter_embedded(self._url(VOCABULARIES), "vocabularies", "List vocabularies"))

    def get_vocabulary(self, name: str) -> Dict[str, Any]:
        """Get a specific vocabulary by name.

        Parameters:
            name: The name of the vocabulary

        Returns:
            Dict[str, Any]: The vocabulary details
        """
        return self._get_json(self._url(VOCABULARIES, f"/{name}"), f"Get vocabulary {name}")

    def vocabulary_for_field(self, metadata_field: str, collection_uuid: str) -> Optional[Dict[str, Any]]:
        """The vocabulary a submission form uses for a field in a collection, or None.

        Parameters:
            metadata_field: The metadata field
            collection_uuid: The UUID of the collection

        Returns:
            Optional[Dict[str, Any]]: The vocabulary details or None if not found
        """
        return self._get_optional(self._url(VOCABULARIES, "/search/byMetadataAndCollection"),
                                  f"Find vocabulary for {metadata_field}",
                                  params={"metadata": metadata_field, "collection": collection_uuid})

    # ------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------

    def iter_entries(self, name: str, filter: Optional[str] = None, exact: bool = False,
                     entry_id: Optional[str] = None, page_size: int = 100,
                     max_results: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """
        Entries ({"display", "value", "authority"?, "otherInformation"?}) of a
        vocabulary. Without filter / entry_id only scrollable vocabularies list
        their entries (the server answers 422 otherwise). A generator.

        Parameters:
            name: The name of the vocabulary
            filter: Optional filter string
            exact: Whether to match the filter exactly
            entry_id: Specific entry ID to retrieve
            page_size: Number of entries per page
            max_results: Maximum number of results to return

        Returns:
            Iterator[Dict[str, Any]]: The vocabulary entries
        """
        if filter is not None and entry_id is not None:
            raise ValueError("Use filter or entry_id, not both")
        if exact and filter is None:
            raise ValueError("exact=True needs a filter")
        params: List[Tuple[str, Any]] = []
        if filter is not None:
            params.append(("filter", filter))
            if exact:
                params.append(("exact", "true"))
        if entry_id is not None:
            params.append(("entryID", entry_id))
        return self._iter_embedded(self._url(VOCABULARIES, f"/{name}/entries"), "entries",
                                   f"Get entries of vocabulary {name}", params, page_size=page_size,
                                   max_results=max_results)

    def get_entries(self, name: str, filter: Optional[str] = None, exact: bool = False,
                    entry_id: Optional[str] = None, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get vocabulary entries as a list.

        Parameters:
            name: The name of the vocabulary
            filter: Optional filter string
            exact: Whether to match the filter exactly
            entry_id: Specific entry ID to retrieve
            max_results: Maximum number of results to return

        Returns:
            List[Dict[str, Any]]: The vocabulary entries
        """
        return list(self.iter_entries(name, filter, exact, entry_id, max_results=max_results))

    def search(self, name: str, text: str, max_results: int = 20) -> List[Dict[str, Any]]:
        """Suggestions for user input (prefix / keyword match, as in the submission form).

        Parameters:
            name: The name of the vocabulary
            text: The text to search for
            max_results: Maximum number of results to return

        Returns:
            List[Dict[str, Any]]: The matching vocabulary entries
        """
        return self.get_entries(name, filter=text, max_results=max_results)

    def lookup(self, name: str, value: str, match_display: bool = True) -> Optional[Dict[str, Any]]:
        """
        The entry whose stored value (or, with match_display, display label)
        is exactly `value`, or None. Results are cached per manager.

        Parameters:
            name: The name of the vocabulary
            value: The value to look up
            match_display: Whether to match against the display label as well

        Returns:
            Optional[Dict[str, Any]]: The matching vocabulary entry or None if not found
        """
        key = (name, value, bool(match_display))
        if key not in self._lookup_cache:
            candidates = self.get_entries(name, filter=value, exact=True, max_results=50)
            match = next((e for e in candidates if e.get("value") == value), None)
            if match is None and match_display:
                match = next((e for e in candidates if e.get("display") == value), None)
            self._lookup_cache[key] = match
        return self._lookup_cache[key]

    def check_values(self, name: str, values: Iterable[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """{value: entry or None} - validate import data against a vocabulary.
        
        Parameters:
            name: The name of the vocabulary
            values: The values to check

        Returns:
            Dict[str, Optional[Dict[str, Any]]]: Mapping of values to their corresponding entries or None if not found
        """
        return {value: self.lookup(name, value) for value in dict.fromkeys(values)}

    def invalid_values(self, name: str, values: Iterable[str]) -> List[str]:
        """The values (in order, no duplicates) that the vocabulary does not contain.
        
        Parameters:
            name: The name of the vocabulary
            values: The values to check

        Returns:
            List[str]: The values not found in the vocabulary
        """
        return [value for value, entry in self.check_values(name, values).items() if entry is None]

    def get_entry_by_id(self, name: str, entry_id: str) -> Optional[Dict[str, Any]]:
        """Entry (as in iter_entries) by its id within the vocabulary, or None.
        
        Parameters:
            name: The name of the vocabulary
            entry_id: The ID of the entry to retrieve

        Returns:
            Optional[Dict[str, Any]]: The entry details or None if not found
        """
        entries = self.get_entries(name, entry_id=entry_id, max_results=1)
        return entries[0] if entries else None

    @staticmethod
    def to_metadata_value(entry: Dict[str, Any], language: Optional[str] = None,
                          confidence: int = CONFIDENCE_ACCEPTED) -> Dict[str, Any]:
        """
        A MetadataValue dict for an entry: the stored value plus, for
        authorities, the authority key and confidence (600 = accepted).

        Parameters:
            entry: The vocabulary entry
            language: Optional language code for the metadata value
            confidence: Confidence level for the authority (default is CONFIDENCE_ACCEPTED)

        Returns:
            Dict[str, Any]: The metadata value dictionary
        """
        value: Dict[str, Any] = {"value": entry.get("value") or entry.get("display")}
        if entry.get("authority"):
            value["authority"] = entry["authority"]
            value["confidence"] = confidence
        if language:
            value["language"] = language
        return value

    # ------------------------------------------------------------------
    # Entry details and hierarchies
    # ------------------------------------------------------------------

    def get_entry_detail(self, vocabulary: str, entry_id: str) -> Dict[str, Any]:
        """Get the details of a vocabulary entry.
        {"id": "srsc:SCB110", "value", "display", "selectable", "otherInformation"}

        Parameters:
            vocabulary: The name of the vocabulary
            entry_id: The ID of the entry to retrieve

        Returns:
            Dict[str, Any]: The entry details
        """
        ident = detail_id(vocabulary, entry_id)
        return self._get_json(self._url(ENTRY_DETAILS, f"/{ident}"), f"Get vocabulary entry {ident}")

    def get_top_entries(self, vocabulary: str) -> List[Dict[str, Any]]:
        """Root entries of a hierarchical vocabulary (422 for flat ones).

        Parameters:
            vocabulary: The name of the vocabulary

        Returns:
            List[Dict[str, Any]]: The top entries of the vocabulary
        """
        return list(self._iter_embedded(self._url(ENTRY_DETAILS, "/search/top"),
                                        ("vocabularyEntryDetails", "entries"), f"Top entries of {vocabulary}",
                                        [("vocabulary", vocabulary)]))

    def get_children(self, vocabulary: str, entry_id: str) -> List[Dict[str, Any]]:
        ident = detail_id(vocabulary, entry_id)
        """Get the children of a vocabulary entry.

        Parameters:
            vocabulary: The name of the vocabulary
            entry_id: The ID of the entry

        Returns:
            List[Dict[str, Any]]: The child entries of the vocabulary entry
        """
        return list(self._iter_embedded(self._url(ENTRY_DETAILS, f"/{ident}/children"),
                                        ("children", "vocabularyEntryDetails"), f"Children of {ident}"))

    def get_parent(self, vocabulary: str, entry_id: str) -> Optional[Dict[str, Any]]:
        """The parent entry detail, None for root entries.

        Parameters:
            vocabulary: The name of the vocabulary
            entry_id: The ID of the entry

        Returns:
            Optional[Dict[str, Any]]: The parent entry details or None if root
        """
        ident = detail_id(vocabulary, entry_id)
        return self._get_optional(self._url(ENTRY_DETAILS, f"/{ident}/parent"), f"Parent of {ident}")

    def get_path(self, vocabulary: str, entry_id: str, max_depth: int = 50) -> List[Dict[str, Any]]:
        """Entry details from the root down to the entry itself.

        Parameters:
            vocabulary: The name of the vocabulary
            entry_id: The ID of the entry
            max_depth: Maximum depth to traverse (default is 50)

        Returns:
            List[Dict[str, Any]]: The path from the root to the entry
        """
        path = [self.get_entry_detail(vocabulary, entry_id)]
        while len(path) < max_depth:
            parent = self.get_parent(vocabulary, path[0]["id"])
            if not parent:
                break
            path.insert(0, parent)
        return path

    def iter_tree(self, vocabulary: str, max_depth: Optional[int] = None,
                  root_id: Optional[str] = None) -> Iterator[Tuple[int, Dict[str, Any]]]:
        """
        Depth-first walk of a hierarchical vocabulary: yields (depth, entry
        detail), from the top entries (or below root_id). Children are only
        requested where otherInformation.hasChildren is not "false".

        Parameters:
            vocabulary: The name of the vocabulary
            max_depth: Optional maximum depth to traverse
            root_id: Optional ID of the root entry to start from

        Yields:
            Tuple[int, Dict[str, Any]]: A tuple of (depth, entry detail) for each entry in the tree
        """
        roots = self.get_children(vocabulary, root_id) if root_id else self.get_top_entries(vocabulary)
        stack = [(0, entry) for entry in reversed(roots)]
        while stack:
            depth, entry = stack.pop()
            yield depth, entry
            if (max_depth is not None and depth >= max_depth) or has_children(entry) is False:
                continue
            for child in reversed(self.get_children(vocabulary, entry["id"])):
                stack.append((depth + 1, child))
