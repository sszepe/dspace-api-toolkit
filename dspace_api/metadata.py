# -*- coding: UTF-8 -*-
"""
Metadata for DSpace API (DSpace 7+ / DSpace-CRIS): JSON Patch builder,
generic metadata editing of any DSpace object, and the metadata registry.

MetadataPatch
    One builder for every place metadata lives - only the base path differs:

        archived objects       /metadata                          (MetadataPatch())
        submission sections    /sections/<form>                   (MetadataPatch.for_section("traditionalpageone"))
        uploaded file          /sections/<upload>/files/<i>/metadata  (MetadataPatch.for_file("upload", 0))

        patch = (MetadataPatch()
                 .set("dc.subject", ["music", "opera"])      # initialise / replace all values
                 .append("dc.contributor.author", "Doe, Jane")
                 .insert("dc.title", 0, {"value": "Main", "language": "en"})
                 .replace_value("dc.title", 1, "Subtitle")
                 .set_property("dc.title", 0, "language", "de")
                 .remove("dc.description")                    # whole field
                 .remove("dc.subject", 1)                     # one value
                 .move("dc.contributor.author", 2, 0))

Registry
    GET|POST         /core/metadataschemas            PUT|DELETE /core/metadataschemas/{id}
    GET|POST         /core/metadatafields?schemaId=   PUT|DELETE /core/metadatafields/{id}
    GET /core/metadatafields/search/bySchema?schema=
    GET /core/metadatafields/search/byFieldName?exactName=|schema=|element=|qualifier=|query=

Fields named in a PATCH path must exist in the registry (else HTTP 422), so
imports can call ensure_fields() / unknown_fields() first.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ._base import BaseManager
from .projection import EmbedArg, ProjectionArg

# Logger for the module
logger = logging.getLogger(__name__)

# REST endpoints for metadata schemas and fields
METADATA_SCHEMAS = "/core/metadataschemas"
METADATA_FIELDS = "/core/metadatafields"

# DSpace object type -> REST collection path (for patch_metadata / get_metadata)
DSO_PATHS = {
    "item": "/core/items",
    "collection": "/core/collections",
    "community": "/core/communities",
    "bundle": "/core/bundles",
    "bitstream": "/core/bitstreams",
    "eperson": "/eperson/epersons",
    "group": "/eperson/groups",
    "site": "/core/sites",
}

ValueArg = Union[str, Dict[str, Any]]


def metadata_value(value: ValueArg, language: Optional[str] = None, authority: Optional[str] = None,
                   confidence: Optional[int] = None) -> Dict[str, Any]:
    """A MetadataValue dict from a string or dict (explicit arguments win over dict keys).
    
    Parameters:
        value (ValueArg): The metadata value, either as a string or a dict.
        language (Optional[str]): The language of the metadata value.
        authority (Optional[str]): The authority key for the metadata value.
        confidence (Optional[int]): The confidence level for the metadata value.

    Returns:
        Dict[str, Any]: The normalized metadata value dictionary.
    """
    result = dict(value) if isinstance(value, dict) else {"value": "" if value is None else str(value)}
    if "value" not in result:
        raise ValueError(f"metadata value needs a 'value' key: {value!r}")
    for key, extra in (("language", language), ("authority", authority), ("confidence", confidence)):
        if extra is not None:
            result[key] = extra
    return result


def parse_field(name: str) -> Tuple[str, str, Optional[str]]:
    """Parse a metadata field name into its components.

    'dc.contributor.author' -> ('dc', 'contributor', 'author'); 'dc.title' -> ('dc', 'title', None)

    Parameters:
        name (str): The full metadata field name in the format 'schema.element[.qualifier]'.

    Returns:
        Tuple[str, str, Optional[str]]: A tuple containing the schema, element, and optional qualifier.
    """
    parts = (name or "").strip().split(".")
    if len(parts) not in (2, 3) or not all(parts):
        raise ValueError(f"Invalid metadata field name {name!r} (expected schema.element[.qualifier])")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else None


def field_name(field: Dict[str, Any]) -> str:
    """Full name of a registry field dict (schema embedded or given as 'schema' prefix).

    Parameters:
        field (Dict[str, Any]): The registry field dictionary containing schema, element, and optional qualifier.

    Returns:
        str: The full metadata field name in the format 'schema.element[.qualifier]'.
    """
    schema = field.get("schema")
    prefix = schema.get("prefix") if isinstance(schema, dict) else schema
    if not prefix:
        prefix = ((field.get("_embedded") or {}).get("schema") or {}).get("prefix")
    return ".".join(p for p in (prefix, field.get("element"), field.get("qualifier")) if p)


class MetadataPatch:
    """Builds JSON Patch operations for metadata (see module docstring)."""

    def __init__(self, base_path: str = "/metadata"):
        """
        Initialize a MetadataPatch instance.

        Parameters:
            base_path (str): The base path for the metadata patch operations.
        """
        self.base_path = base_path.rstrip("/")
        self.operations: List[Dict[str, Any]] = []

    @classmethod
    def for_section(cls, section: str) -> "MetadataPatch":
        """Metadata of a submission-form section of a workspace/workflow item.

        Parameters:
            section (str): The section identifier.

        Returns:
            MetadataPatch: A new MetadataPatch instance for the specified section.
        """
        return cls(f"/sections/{section}")

    @classmethod
    def for_file(cls, upload_section: str, file_index: int) -> "MetadataPatch":
        """Metadata of the file at `file_index` in an upload section.

        Parameters:
            upload_section (str): The upload section identifier.
            file_index (int): The index of the file within the upload section.

        Returns:
            MetadataPatch: A new MetadataPatch instance for the specified file.
        """
        return cls(f"/sections/{upload_section}/files/{int(file_index)}/metadata")

    def _path(self, field: str, *rest) -> str:
        """
        Construct the full path for a metadata field, optionally with additional path components.

        Parameters:
            field (str): The metadata field name.
            *rest: Additional path components to append.

        Returns:
            str: The full path for the metadata field.
        """
        parse_field(field)
        return "/".join([self.base_path, field, *(str(r) for r in rest)])

    def _op(self, op: str, path: str, **extra) -> "MetadataPatch":
        """
        Add a metadata patch operation to the list of operations.

        Parameters:
            op (str): The operation type (e.g., 'add', 'replace', 'remove').
            path (str): The path of the metadata field to operate on.
            **extra: Additional keyword arguments for the operation.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        self.operations.append({"op": op, "path": path, **extra})
        return self

    # -- whole field ----------------------------------------------------------

    @property
    def archived(self) -> bool:
        """Check if the metadata patch is for archived objects.

        Returns:
            bool: True for archived objects (/metadata), False for submission sections.
        """
        return self.base_path == "/metadata"

    def _set_by_appending(self, field: str, items) -> "MetadataPatch":
        """
        Set the metadata field by appending each item individually.

        Parameters:
            field (str): The metadata field name.
            items (Sequence[Dict[str, Any]]): The list of metadata values to append.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        # DSpace applies only the first value of an array in add/replace on
        # /metadata/<field>: clear the field, then append every value
        self._op("remove", self._path(field))
        for item in items:
            self._op("add", self._path(field, "-"), value=item)
        return self

    def set(self, field: str, values: Union[ValueArg, Sequence[ValueArg]], language: Optional[str] = None
            ) -> "MetadataPatch":
        """
        Initialise the field or replace ALL its values. Archived objects:
        'remove' + one 'add .../-' per value (works whether or not the field
        has values). Submission sections: 'add' with the array.

        Parameters:
            field (str): The metadata field name.
            values (Union[ValueArg, Sequence[ValueArg]]): The value(s) to set for the field.
            language (Optional[str]): The language of the metadata values.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        items = [metadata_value(v, language) for v in (values if isinstance(values, (list, tuple)) else [values])]
        if self.archived:
            return self._set_by_appending(field, items)
        return self._op("add", self._path(field), value=items)

    def replace(self, field: str, values: Union[ValueArg, Sequence[ValueArg]], language: Optional[str] = None
                ) -> "MetadataPatch":
        """'replace' all values of an EXISTING field (several values on archived objects: like set()).

        Parameters:
            field (str): The metadata field name.
            values (Union[ValueArg, Sequence[ValueArg]]): The value(s) to replace for the field.
            language (Optional[str]): The language of the metadata values.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        items = [metadata_value(v, language) for v in (values if isinstance(values, (list, tuple)) else [values])]
        if self.archived and len(items) > 1:
            return self._set_by_appending(field, items)
        return self._op("replace", self._path(field), value=items)

    def remove(self, field: str, index: Optional[int] = None) -> "MetadataPatch":
        """
        Remove the whole field, or only the value at `index`.

        Parameters:
            field (str): The metadata field name.
            index (Optional[int]): The index of the value to remove. If None, remove the whole field.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        path = self._path(field) if index is None else self._path(field, int(index))
        return self._op("remove", path)

    # -- single values --------------------------------------------------------

    def append(self, field: str, value: ValueArg, language: Optional[str] = None) -> "MetadataPatch":
        """Add one value at the end. The field must already have values - use set() for the first one.
        
        Parameters:
            field (str): The metadata field name.
            value (ValueArg): The value to append.
            language (Optional[str]): The language of the metadata value.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        return self._op("add", self._path(field, "-"), value=metadata_value(value, language))

    def insert(self, field: str, index: int, value: ValueArg, language: Optional[str] = None) -> "MetadataPatch":
        """
        Insert one value at position `index` (0-based); following values shift.
        Note: on archived objects DSpace (8-10, CRIS) inserts at position 0
        for any index other than '-' - use append() + move() for a position > 0.

        Parameters:
            field (str): The metadata field name.
            index (int): The position to insert the value at (0-based).
            value (ValueArg): The value to insert.
            language (Optional[str]): The language of the metadata value.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        return self._op("add", self._path(field, int(index)), value=metadata_value(value, language))

    def replace_value(self, field: str, index: int, value: ValueArg, language: Optional[str] = None
                      ) -> "MetadataPatch":
        """
        Replace one value at position `index` (0-based).

        Parameters:
            field (str): The metadata field name.
            index (int): The position of the value to replace (0-based).
            value (ValueArg): The new value to set.
            language (Optional[str]): The language of the metadata value.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        return self._op("replace", self._path(field, int(index)), value=metadata_value(value, language))

    def set_property(self, field: str, index: int, prop: str, value: Any) -> "MetadataPatch":
        """
        Replace one property (value, language, authority, confidence) of one value.

        Parameters:
            field (str): The metadata field name.
            index (int): The index of the value to modify.
            prop (str): The property to replace ('value', 'language', 'authority', 'confidence').
            value (Any): The new value for the property.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        if prop not in ("value", "language", "authority", "confidence"):
            raise ValueError(f"Unknown metadata value property {prop!r}")
        return self._op("replace", self._path(field, int(index), prop), value=value)

    def move(self, field: str, from_index: int, to_index: int) -> "MetadataPatch":
        """Re-order values of a repeatable field.
        
        Parameters:
            field (str): The metadata field name.
            from_index (int): The current position of the value to move (0-based).
            to_index (int): The new position of the value (0-based).

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        return self._op("move", self._path(field, int(to_index)), **{"from": self._path(field, int(from_index))})

    # -- helpers ----------------------------------------------------------------

    def extend(self, other: "MetadataPatch") -> "MetadataPatch":
        """Extend the current patch with another patch.

        Parameters:
            other (MetadataPatch): Another MetadataPatch instance to merge into this one.

        Returns:
            MetadataPatch: The current MetadataPatch instance.
        """
        self.operations.extend(other.operations)
        return self

    def fields(self) -> List[str]:
        """Metadata fields touched by this patch (for registry checks).

        Returns:
            List[str]: A list of metadata field names affected by this patch.
        """
        names = []
        for op in self.operations:
            for path in (op["path"], op.get("from")):
                if path:
                    name = path[len(self.base_path) + 1:].split("/", 1)[0]
                    if name not in names:
                        names.append(name)
        return names

    def __len__(self):
        """Number of operations in this patch."""
        return len(self.operations)

    def __iter__(self):
        """Iterate over the operations in this patch."""
        return iter(self.operations)

    def __repr__(self):
        """String representation of the MetadataPatch."""
        return f"MetadataPatch({self.base_path!r}, {len(self)} op(s))"


def as_operations(patch: Union[MetadataPatch, Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Convert a MetadataPatch or an iterable of raw operations into a list of operations."""
    return list(patch.operations if isinstance(patch, MetadataPatch) else patch)


class MetadataManager(BaseManager):
    """Metadata registry and metadata PATCH for any DSpace object"""

    def __init__(self, auth):
        """Initialize the MetadataManager with authentication."""
        super().__init__(auth)
        self._field_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._schema_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Metadata of any DSpace object
    # ------------------------------------------------------------------

    def _dso_url(self, dso_type: str, uuid: str) -> str:
        """Construct the URL for a DSpace object based on its type and UUID.

        Parameters:
            dso_type (str): The type of the DSpace object (e.g., 'item', 'collection').
            uuid (str): The UUID of the DSpace object.

        Returns:
            str: The URL for the specified DSpace object.

        Raises:
            ValueError: If the dso_type is not recognized.
        """
        if dso_type not in DSO_PATHS:
            raise ValueError(f"dso_type must be one of {sorted(DSO_PATHS)}, not {dso_type!r}")
        return self._url(DSO_PATHS[dso_type], f"/{uuid}")

    def get_metadata(self, dso_type: str, uuid: str, all_languages: Optional[bool] = True
                     ) -> Dict[str, List[Dict[str, Any]]]:
        """Retrieve the metadata map of a DSpace object.

        By default, retrieves all languages so that indexes match PATCH positions.

        Parameters:
            dso_type (str): The type of the DSpace object (e.g., 'item', 'collection').
            uuid (str): The UUID of the DSpace object.
            all_languages (Optional[bool]): Whether to retrieve metadata in all languages. Defaults to True.

        Returns:
            Dict[str, List[Dict[str, Any]]]: The metadata map of the object.
        """
        return self._get_json(self._dso_url(dso_type, uuid), f"Get {dso_type} {uuid}",
                              all_languages=all_languages).get("metadata") or {}

    def patch_metadata(self, dso_type: str, uuid: str, patch: Union[MetadataPatch, Iterable[Dict[str, Any]]],
                       check_fields: bool = False) -> Dict[str, Any]:
        """
        Apply a MetadataPatch (or raw operations) to an item, collection,
        community, bundle, bitstream, eperson, group or the site.
        check_fields=True verifies the fields exist in the registry first and
        raises ValueError listing the unknown ones (instead of a bare 422).

        Parameters:
            dso_type (str): The type of the DSpace object (e.g., 'item', 'collection').
            uuid (str): The UUID of the DSpace object.
            patch (Union[MetadataPatch, Iterable[Dict[str, Any]]]): The metadata patch to apply.
            check_fields (bool): Whether to check that the fields exist in the registry before applying. Defaults to False.

        Returns:
            Dict[str, Any]: The response from the DSpace API after applying the patch.
        """
        operations = as_operations(patch)
        if not operations:
            raise ValueError("Empty metadata patch")
        if check_fields and isinstance(patch, MetadataPatch):
            unknown = self.unknown_fields(patch.fields())
            if unknown:
                raise ValueError(f"Unknown metadata field(s), not in the registry: {unknown}")
        logger.info("Patching metadata of %s %s (%d op(s))", dso_type, uuid, len(operations))
        return self._request("PATCH", self._dso_url(dso_type, uuid), f"Patch metadata of {dso_type} {uuid}",
                             headers=self._headers(), json=operations).json()

    # ------------------------------------------------------------------
    # Schemas
    # ------------------------------------------------------------------

    def get_schemas(self) -> List[Dict[str, Any]]:
        """List all metadata schemas.

        Returns:
            List[Dict[str, Any]]: A list of metadata schemas.
        """
        return list(self._iter_embedded(self._url(METADATA_SCHEMAS), "metadataschemas", "List metadata schemas"))

    def get_schema(self, schema_id: int) -> Dict[str, Any]:
        """Retrieve a metadata schema by its ID.

        Parameters:
            schema_id (int): The ID of the metadata schema.

        Returns:
            Dict[str, Any]: The metadata schema.
        """
        return self._get_json(self._url(METADATA_SCHEMAS, f"/{schema_id}"), f"Get metadata schema {schema_id}")

    def find_schema(self, prefix: str, refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        Retrieve a metadata schema by its prefix.

        Parameters:
            prefix (str): The prefix of the metadata schema (e.g., 'dc', 'dcterms', 'crisrp').
            refresh (bool): Whether to refresh the schema cache. Defaults to False.

        Returns:
            Optional[Dict[str, Any]]: The metadata schema if found, or None.
        """
        if refresh or prefix not in self._schema_cache:
            self._schema_cache = {s.get("prefix"): s for s in self.get_schemas()}
        return self._schema_cache.get(prefix)

    def create_schema(self, prefix: str, namespace: str) -> Dict[str, Any]:
        """Create a new metadata schema.

        Admin only. 422 for an empty prefix, dots/commas/spaces, > 32 chars, or empty namespace.

        Parameters:
            prefix (str): The prefix of the metadata schema (e.g., 'dc', 'dcterms', 'crisrp').
            namespace (str): The namespace URI of the metadata schema.

        Returns:
            Dict[str, Any]: The created metadata schema.
        """
        schema = self._request("POST", self._url(METADATA_SCHEMAS), f"Create metadata schema {prefix}",
                               expected=(200, 201), headers=self._headers(),
                               json={"prefix": prefix, "namespace": namespace}).json()
        self._schema_cache[prefix] = schema
        logger.info("Created metadata schema %s (%s)", prefix, schema.get("id"))
        return schema

    def update_schema(self, schema_id: int, namespace: str) -> Dict[str, Any]:
        """Change the namespace (the prefix cannot be changed).

        Parameters:
            schema_id (int): The ID of the metadata schema.
            namespace (str): The new namespace URI for the metadata schema.

        Returns:
            Dict[str, Any]: The updated metadata schema.
        """
        current = self.get_schema(schema_id)
        body = {"id": schema_id, "prefix": current["prefix"], "namespace": namespace}
        schema = self._request("PUT", self._url(METADATA_SCHEMAS, f"/{schema_id}"),
                               f"Update metadata schema {schema_id}", headers=self._headers(), json=body).json()
        self._schema_cache.pop(current["prefix"], None)
        return schema

    def delete_schema(self, schema_id: int) -> bool:
        """Delete a metadata schema.

        Admin only. 422 if the schema is in use.

        Parameters:
            schema_id (int): The ID of the metadata schema to delete.

        Returns:
            bool: True if the schema was successfully deleted.
        """
        self._request("DELETE", self._url(METADATA_SCHEMAS, f"/{schema_id}"), f"Delete metadata schema {schema_id}",
                      expected=(204,))
        self._schema_cache.clear()
        self._field_cache.clear()
        return True

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------

    def get_field(self, field_id: int, embed: EmbedArg = "schema") -> Dict[str, Any]:
        """Retrieve a metadata field by its ID.

        Parameters:
            field_id (int): The ID of the metadata field.
            embed (EmbedArg): The embed parameter for the request. Defaults to "schema".

        Returns:
            Dict[str, Any]: The metadata field.
        """
        return self._get_json(self._url(METADATA_FIELDS, f"/{field_id}"), f"Get metadata field {field_id}",
                              embed=embed)

    def get_fields_by_schema(self, prefix: str) -> List[Dict[str, Any]]:
        """Retrieve all metadata fields for a given schema prefix.

        Parameters:
            prefix (str): The schema prefix (e.g., 'dc', 'dcterms', 'crisrp').

        Returns:
            List[Dict[str, Any]]: A list of metadata fields belonging to the specified schema.
        """
        url = self._url(METADATA_FIELDS, "/search/bySchema")
        return list(self._iter_embedded(url, "metadatafields", f"List fields of schema {prefix}",
                                        [("schema", prefix)]))

    def search_fields(self, query: Optional[str] = None, schema: Optional[str] = None,
                      element: Optional[str] = None, qualifier: Optional[str] = None,
                      exact_name: Optional[str] = None, max_results: Optional[int] = None,
                      projection: ProjectionArg = None) -> List[Dict[str, Any]]:
        """Search metadata fields by various criteria.

        Every argument is optional and they combine.

        Parameters:
            query (Optional[str]): The search query.
            schema (Optional[str]): The schema prefix to filter by.
            element (Optional[str]): The element name to filter by.
            qualifier (Optional[str]): The qualifier to filter by.
            exact_name (Optional[str]): The exact field name to match.
            max_results (Optional[int]): The maximum number of results to return.
            projection (ProjectionArg): The projection parameter for the request.

        Returns:
            List[Dict[str, Any]]: A list of metadata fields matching the criteria.
        """
        params = [(k, v) for k, v in (("query", query), ("schema", schema), ("element", element),
                                      ("qualifier", qualifier), ("exactName", exact_name)) if v]
        url = self._url(METADATA_FIELDS, "/search/byFieldName")
        return list(self._iter_embedded(url, "metadatafields", "Search metadata fields", params,
                                        max_results=max_results, projection=projection, embed="schema"))

    def find_field(self, name: str, refresh: bool = False) -> Optional[Dict[str, Any]]:
        """The registry field for 'schema.element[.qualifier]', or None (cached).

        Parameters:
            name (str): The full name of the metadata field in the format 'schema.element[.qualifier]'.
            refresh (bool): If True, refresh the cache and fetch the field from the server.

        Returns:
            Optional[Dict[str, Any]]: The metadata field if found, otherwise None.
        """
        parse_field(name)
        if refresh or name not in self._field_cache:
            matches = [f for f in self.search_fields(exact_name=name) if self._same_field(f, name)]
            self._field_cache[name] = matches[0] if matches else None
        return self._field_cache[name]

    @staticmethod
    def _same_field(field: Dict[str, Any], name: str) -> bool:
        """Check if a metadata field matches the given full name.

        exactName should return only the match; verify anyway (schema prefix only if the server sent it).

        Parameters:
            field (Dict[str, Any]): The metadata field to check.
            name (str): The full name of the metadata field in the format 'schema.element[.qualifier]'.

        Returns:
            bool: True if the field matches the given name, False otherwise.
        """
        _prefix, element, qualifier = parse_field(name)
        if field.get("schema") or (field.get("_embedded") or {}).get("schema"):
            return field_name(field) == name
        return field.get("element") == element and (field.get("qualifier") or None) == qualifier

    def field_exists(self, name: str) -> bool:
        """Check if a metadata field exists in the registry.

        Parameters:
            name (str): The full name of the metadata field in the format 'schema.element[.qualifier]'.

        Returns:
            bool: True if the field exists, False otherwise.
        """
        return self.find_field(name) is not None

    def unknown_fields(self, names: Iterable[str]) -> List[str]:
        """The names (in order, no duplicates) that are not in the registry.

        Parameters:
            names (Iterable[str]): A list of metadata field names to check.

        Returns:
            List[str]: A list of metadata field names that are not found in the registry.
        """
        seen, unknown = set(), []
        for name in names:
            if name not in seen:
                seen.add(name)
                if not self.field_exists(name):
                    unknown.append(name)
        return unknown

    def create_field(self, name: str, scope_note: Optional[str] = None) -> Dict[str, Any]:
        """Register 'schema.element[.qualifier]' (admin only; the schema must exist).
        
        Parameters:
            name (str): The full name of the metadata field in the format 'schema.element[.qualifier]'.
            scope_note (Optional[str]): The scope note for the metadata field.

        Returns:
            Dict[str, Any]: The created metadata field.
        """
        prefix, element, qualifier = parse_field(name)
        schema = self.find_schema(prefix)
        if not schema:
            raise ValueError(f"Metadata schema {prefix!r} does not exist - create_schema() first")
        body = {"element": element, "qualifier": qualifier, "scopeNote": scope_note}
        field = self._request("POST", self._url(METADATA_FIELDS), f"Create metadata field {name}",
                              expected=(200, 201), headers=self._headers(), params={"schemaId": schema["id"]},
                              json=body).json()
        self._field_cache[name] = field
        logger.info("Created metadata field %s (%s)", name, field.get("id"))
        return field

    def ensure_fields(self, names: Iterable[str], scope_note: Optional[str] = None, dry_run: bool = False
                      ) -> List[str]:
        """Create the fields that are missing. Returns the names created (or, with dry_run, to create).
        
        Parameters:
            names (Iterable[str]): A list of metadata field names to ensure.
            scope_note (Optional[str]): The scope note for the metadata fields.
            dry_run (bool): If True, do not actually create the fields, just return the names that would be created.

        Returns:
            List[str]: The names of the metadata fields that were created (or would be created with dry_run).
        """
        missing = self.unknown_fields(names)
        if not dry_run:
            for name in missing:
                self.create_field(name, scope_note)
        return missing

    def update_field(self, field_id: int, scope_note: Optional[str]) -> Dict[str, Any]:
        """Change the scope note (element and qualifier cannot be changed).
        
        Parameters:
            field_id (int): The ID of the metadata field to update.
            scope_note (Optional[str]): The new scope note for the metadata field.

        Returns:
            Dict[str, Any]: The updated metadata field.
        """
        current = self.get_field(field_id, embed=None)
        body = {"id": field_id, "element": current["element"], "qualifier": current.get("qualifier"),
                "scopeNote": scope_note}
        field = self._request("PUT", self._url(METADATA_FIELDS, f"/{field_id}"), f"Update metadata field {field_id}",
                              headers=self._headers(), json=body).json()
        self._field_cache.clear()
        return field

    def delete_field(self, field_id: int) -> bool:
        """Delete a metadata field by its ID.

        Parameters:
            field_id (int): The ID of the metadata field to delete.

        Returns:
            bool: True if the field was successfully deleted.
        """
        self._request("DELETE", self._url(METADATA_FIELDS, f"/{field_id}"), f"Delete metadata field {field_id}",
                      expected=(204,))
        self._field_cache.clear()
        return True
