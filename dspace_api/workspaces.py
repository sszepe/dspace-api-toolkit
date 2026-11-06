# -*- coding: UTF-8 -*-
"""
Workspace items (in-progress submissions) for DSpace API (DSpace 7+ / DSpace-CRIS)

    POST   /submission/workspaceitems?owningCollection=        empty submission
    POST   /submission/workspaceitems?owningCollection=        text/uri-list: import an external entry
    POST   /submission/workspaceitems?owningCollection=        multipart: create from file(s)
    GET    /submission/workspaceitems[/{id}]                    list / single
    GET    /submission/workspaceitems/search/findBySubmitter?uuid=
    GET    /submission/workspaceitems/search/item?uuid=         workspace item of an item
    GET    /submission/workspaceitems/{id}/item|collection|submissionDefinition
    POST   /submission/workspaceitems/{id}                      multipart: upload a file
    PATCH  /submission/workspaceitems/{id}                      sections (see below)
    DELETE /submission/workspaceitems/{id}
    POST   /workflow/workflowitems                              deposit (text/uri-list)

Section PATCH helpers, by section type:
    submission-form   patch_section_metadata(), set_metadata(), fill_metadata() (routes fields to forms)
    upload            upload_file(), patch_file_metadata(), add/remove_file_access_condition(),
                      remove_file(), move_file(), set/clear_primary_file()
    license           grant_license()
    access            add/set/remove_access_condition(s), set_discoverable()
    cclicense         set_cc_license(), remove_cc_license(), get_cc_license_url()
    sherpaPolicies    refresh_sherpa_policies()

Submission configuration (config/submission*): definitions, sections, forms,
upload and access options, CC licenses - used to find section names by type
and to route metadata fields to the form that contains them.

submit() runs a whole deposit: create, fill metadata, upload files, grant the
license, deposit, and returns validation errors instead of a bare 422.
"""

import logging
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ._base import BaseManager
from .config import Endpoints
from .exceptions import DSpaceError
from .metadata import MetadataPatch, as_operations
from .projection import EmbedArg, ProjectionArg, view_params
from .exceptions import handle_response_error, BadRequestError

# Logger for the module
logger = logging.getLogger(__name__)


# API endpoints and section types for workspace items
CONFIG = "/config"
SECTION_TYPES = ("submission-form", "upload", "license", "cclicense", "access", "collection",
                 "identifiers", "duplicates", "sherpaPolicies", "coarnotify")



def _embedded_list(value, *keys) -> Optional[List[Dict[str, Any]]]:
    """A HAL embedded list: a plain list, or a page {"_embedded": {<key>: [...]}}; None if absent.
    
    Parameters:
        value: The value to extract the embedded list from
        *keys: Keys to look for in the "_embedded" dictionary

    Returns:
        Optional[List[Dict[str, Any]]]: The extracted list or None if not found
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        inner = value.get("_embedded") or {}
        for key in keys:
            if isinstance(inner.get(key), list):
                return inner[key]
    return None

class SubmissionError(DSpaceError):
    """A submission could not be completed; .errors holds DSpace's validation errors."""

    def __init__(self, message, errors=None, workspace_item=None, status_code=None, response_text=None):
        """Initialize a SubmissionError instance.

        Args:
            message: Error message
            errors: List of validation errors from DSpace
            workspace_item: The workspace item associated with the error
            status_code: HTTP status code of the response
            response_text: Text of the HTTP response
        """
        super().__init__(message, status_code=status_code, response_text=response_text)
        self.errors = errors or []
        self.workspace_item = workspace_item


class WorkspaceManager(BaseManager):
    """Manages DSpace workspace items"""

    def __init__(self, auth):
        """Initialize a WorkspaceManager instance."""
        super().__init__(auth)
        self._definition_cache: Dict[str, Dict[str, Any]] = {}
        self._form_fields_cache: Dict[str, List[str]] = {}

    def create_workspace_item(self, collection_uuid, entity_type="Publication"):
        """
        Create a new workspace item in a collection

        Parameters:
            collection_uuid: UUID of the collection
            entity_type: Type of entity (e.g., "Publication")

        Returns:
            dict: Created workspace item data
        """
        logger.info("Creating workspace item in collection %s", collection_uuid)

        url = (f"{self.config.dspace_url}{Endpoints.WORKSPACE_ITEMS}"
               f"?projection=full&owningCollection={collection_uuid}&entityType={entity_type}")

        try:
            response = self.session.post(
                url,
                headers=self.config.headers,
                data={},
                timeout=self.config.timeout
            )

            if response.status_code == 201:
                logger.info("Workspace item created successfully")
                return response.json()
            else:
                handle_response_error(response, "Create workspace item")

        except Exception as e:
            logger.error("Failed to create workspace item: %s", e)
            raise

    def get_workspace_item(self, workspace_id, all_languages=None, projection=None, embed=None):
        """
        Get a specific workspace item by ID

        Parameters:
            workspace_id: ID of the workspace item

        Returns:
            dict: Workspace item data
        """
        logger.info("Getting workspace item %s", workspace_id)

        url = f"{self.config.dspace_url}{Endpoints.WORKSPACE_ITEMS}/{workspace_id}"
        params = view_params(None, self.config, all_languages, projection, embed)

        try:
            response = self.session.get(
                url,
                headers=self.config.headers,
                params=params,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Successfully retrieved workspace item %s", workspace_id)
                return response.json()
            if response.status_code == 422:
                return {"error": "Workspace item cannot be deposited automatically."}
            handle_response_error(response, "Get workspace item %s" % workspace_id)

        except Exception as e:
            logger.error("Failed to get workspace item %s: %s", workspace_id, e)
            raise

    def patch_workspace_item(self, workspace_id, operations):
        """
        Patch a workspace item with JSON Patch operations

        Parameters:
            workspace_id: ID of the workspace item
            operations: List of JSON Patch operations

        Returns:
            dict: Updated workspace item data
        """
        logger.info("Patching workspace item %s", workspace_id)

        url = f"{self.config.dspace_url}{Endpoints.WORKSPACE_ITEMS}/{workspace_id}"

        try:
            response = self.session.patch(
                url,
                headers={**self.config.headers, "Content-Type": "application/json-patch+json"},
                json=operations,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info(f"Workspace item {workspace_id} patched successfully")
                return response.json()
            if response.status_code == 422:
                return {"error": "Workspace item cannot be deposited automatically."}
            handle_response_error(response, f"Patch workspace item {workspace_id}")

        except Exception as e:
            logger.error(f"Failed to patch workspace item %s: %s", workspace_id, e)
            raise

    def add_metadata(self, workspace_id, section, field, values):
        """
        Add metadata to a workspace item

        Parameters:
            workspace_id: ID of the workspace item
            section: Section name (e.g., "publication", "publication_type")
            field: Metadata field name (e.g., "dc.title")
            values: List of metadata values

        Returns:
            dict: Updated workspace item data
        """
        if isinstance(values, str):
            values = [{"value": values}]
        elif isinstance(values, list) and all(isinstance(v, str) for v in values):
            values = [{"value": v} for v in values]

        operation = {
            "op": "add",
            "path": f"/sections/{section}/{field}",
            "value": values
        }

        return self.patch_workspace_item(workspace_id, [operation])

    def set_metadata(self, workspace_id, metadata_sections):
        """
        Set multiple metadata fields at once

        Parameters:
            workspace_id: ID of the workspace item
            metadata_sections: Dictionary of sections and their metadata
                Example: {
                    "publication": {
                        "dc.title": [{"value": "Title", "language": "en"}],
                        "dc.date.issued": [{"value": "2025"}]
                    },
                    "publication_type": {
                        "dc.type": [{
                            "value": "still image",
                            "authority": "publication-coar-types:c_ecc8",
                            "confidence": 600
                        }]
                    }
                }

        Returns:
            dict: Updated workspace item data
        """
        operations = []

        for section, fields in metadata_sections.items():
            for field, values in fields.items():
                operations.append({
                    "op": "add",
                    "path": f"/sections/{section}/{field}",
                    "value": values
                })

        return self.patch_workspace_item(workspace_id, operations)

    def delete_workspace_item(self, workspace_id):
        """
        Delete a workspace item

        Parameters:
            workspace_id: ID of the workspace item to delete

        Returns:
            bool: True if deletion was successful
        """
        logger.info("Deleting workspace item %s", workspace_id)

        url = f"{self.config.dspace_url}{Endpoints.WORKSPACE_ITEMS}/{workspace_id}"

        try:
            response = self.session.delete(
                url,
                headers=self.config.headers,
                timeout=self.config.timeout
            )

            if response.status_code == 204:
                logger.info("Workspace item %s deleted successfully", workspace_id)
                return True
            else:
                handle_response_error(response, f"Delete workspace item {workspace_id}")

        except Exception as e:
            logger.error("Failed to delete workspace item %s: %s", workspace_id, e)
            raise

    def deposit_to_workflow(self, workspace_id):
        """
        Deposit a workspace item to the workflow (archive)

        Parameters:
            workspace_id: ID of the workspace item to deposit

        Returns:
            str: Response text (usually contains workflow item info)
        """
        logger.info("Depositing workspace item %s to workflow", workspace_id)

        workspace_url = f"{self.config.dspace_url}{Endpoints.WORKSPACE_ITEMS}/{workspace_id}"
        deposit_url = f"{self.config.dspace_url}{Endpoints.WORKFLOW_ITEMS}?projection=full"

        headers = {"Content-Type": "text/uri-list"}

        try:
            response = self.session.post(
                deposit_url,
                headers=headers,
                data=workspace_url,
                timeout=self.config.timeout
            )

            if response.status_code == 201:
                logger.info("Workspace item deposited successfully")
                return response.text
            else:
                handle_response_error(response, "Deposit workspace item")

        except Exception as e:
            logger.error("Failed to deposit workspace item: %s", e)
            raise

    def get_workspace_items(self, page=0, size=20, all_languages=None, projection=None, embed=None):
        """
        Get list of workspace items

        Parameters:
            page: Page number (0-based)
            size: Number of items per page

        Returns:
            dict: API response containing workspace items
        """
        logger.info("Getting workspace items (page=%d, size=%d)", page, size)

        url = f"{self.config.dspace_url}{Endpoints.WORKSPACE_ITEMS}"
        params = {"page": page, "size": size}
        params = view_params(params, self.config, all_languages, projection, embed)

        try:
            response = self.session.get(
                url,
                headers=self.config.headers,
                params=params,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Successfully retrieved workspace items")
                return response.json()
            else:
                handle_response_error(response, "Get workspace items")

        except Exception as e:
            logger.error("Failed to get workspace items: %s", e)
            raise

    # Specialized functions to ease project based development

    def create_publication_item(self, collection_uuid, title, description=None,
                              date_issued=None, pub_type="still image",
                              pub_type_authority="publication-coar-types:c_ecc8"):
        """
        Convenience method to create a publication workspace item with basic metadata

        Parameters:
            collection_uuid: UUID of the collection
            title: Title of the publication
            description: Optional description
            date_issued: Publication date (year)
            pub_type: Publication type value
            pub_type_authority: Authority for publication type

        Returns:
            dict: Created and populated workspace item
        """
        # Create workspace item
        workspace_item = self.create_workspace_item(collection_uuid, "Publication")
        workspace_id = workspace_item.get("id")

        if not workspace_id:
            raise BadRequestError("Failed to get workspace item ID")

        # Prepare metadata sections
        metadata_sections = {
            "publication_type": {
                "dc.type": [{
                    "value": pub_type,
                    "language": None,
                    "authority": pub_type_authority,
                    "securityLevel": 0,
                    "confidence": 600,
                    "place": 0
                }]
            },
            "publication": {
                "dc.title": [{"value": title, "language": "en"}]
            }
        }

        if date_issued:
            metadata_sections["publication"]["dc.date.issued"] = [{"value": str(date_issued)}]

        if description:
            metadata_sections["publication_indexing"] = {
                "dc.description": [{"value": description, "language": "en"}]
            }

        # Set metadata
        return self.set_metadata(workspace_id, metadata_sections)

    # ======================================================================
    # Everything below follows the BaseManager conventions (retries, paging,
    # projection/embed) and raises SubmissionError with DSpace's validation
    # errors where the older methods above return {"error": ...} on 422.
    # ======================================================================

    # Workspace item URL construction
    WS = Endpoints.WORKSPACE_ITEMS

    def _ws_url(self, ws_id, suffix: str = "") -> str:
        """Construct the URL for a workspace item.

        Parameters:
            ws_id: The ID of the workspace item
            suffix: Optional suffix to append to the URL

        Returns:
            str: The constructed URL
        """
        return self._url(self.WS, f"/{ws_id}{suffix}")

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, ws_id, all_languages: Optional[bool] = None, projection: ProjectionArg = None,
            embed: EmbedArg = None) -> Dict[str, Any]:
        """A workspace item (raises on errors, unlike get_workspace_item()).
        
        Parameters:
            ws_id: The ID of the workspace item
            all_languages: Whether to include all languages
            projection: Projection argument for the request
            embed: Embed argument for the request

        Returns:
            Dict[str, Any]: The workspace item
        """
        return self._get_json(self._ws_url(ws_id), f"Get workspace item {ws_id}", all_languages=all_languages,
                              projection=projection, embed=embed)

    def iter_workspace_items(self, page_size: Optional[int] = None, embed: EmbedArg = None):
        """All workspace items the user may see (admins: all). A generator.
        
        Parameters:
            page_size: The number of items per page
            embed: Embed argument for the request

        Yields:
            Dict[str, Any]: Workspace items
        """
        return self._iter_embedded(self._url(self.WS), "workspaceitems", "List workspace items",
                                   page_size=page_size, embed=embed)

    def find_by_submitter(self, eperson_uuid: str, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Find workspace items submitted by a specific user.

        Parameters:
            eperson_uuid: The UUID of the submitter
            embed: Embed argument for the request

        Returns:
            List[Dict[str, Any]]: Workspace items submitted by the user
        """
        url = self._url(self.WS, "/search/findBySubmitter")
        return list(self._iter_embedded(url, "workspaceitems", f"Find workspace items of {eperson_uuid}",
                                        [("uuid", eperson_uuid)], embed=embed))

    def find_by_item(self, item_uuid: str, embed: EmbedArg = None) -> Optional[Dict[str, Any]]:
        """The workspace item holding this (not yet archived) item, or None (204).
        
        Parameters:
            item_uuid: The UUID of the item
            embed: Embed argument for the request

        Returns:
            Optional[Dict[str, Any]]: The workspace item or None if not found
        """
        return self._get_optional(self._url(self.WS, "/search/item"), f"Find workspace item of item {item_uuid}",
                                  params={"uuid": item_uuid}, embed=embed)

    def get_item_of(self, ws_id, all_languages: Optional[bool] = None) -> Dict[str, Any]:
        """The item behind the submission (its uuid stays the same after deposit).

        Parameters:
            ws_id: The ID of the workspace item
            all_languages: Whether to include all languages

        Returns:
            Dict[str, Any]: The item
        """
        return self._get_json(self._ws_url(ws_id, "/item"), f"Get item of workspace item {ws_id}",
                              all_languages=all_languages)

    def get_collection_of(self, ws_id) -> Dict[str, Any]:
        """The collection behind the workspace item.

        Parameters:
            ws_id: The ID of the workspace item

        Returns:
            Dict[str, Any]: The collection
        """
        return self._get_json(self._ws_url(ws_id, "/collection"), f"Get collection of workspace item {ws_id}")

    def get_definition_of(self, ws_id) -> Dict[str, Any]:
        """The submission definition behind the workspace item.

        Parameters:
            ws_id: The ID of the workspace item

        Returns:
            Dict[str, Any]: The submission definition
        """
        return self._get_json(self._ws_url(ws_id, "/submissionDefinition"),
                              f"Get submission definition of workspace item {ws_id}")

    def delete(self, ws_id) -> bool:
        """Delete the submission and its item.
        
        Parameters:
            ws_id: The ID of the workspace item

        Returns:
            bool: True if the deletion was successful
        """
        self._request("DELETE", self._ws_url(ws_id), f"Delete workspace item {ws_id}", expected=(204,))
        return True

    # ------------------------------------------------------------------
    # Creating submissions
    # ------------------------------------------------------------------

    def start_submission(self, collection_uuid: str, entity_type: Optional[str] = None) -> Dict[str, Any]:
        """Empty workspace item in the collection (entity_type only where entities are configured).
        
        Parameters:
            collection_uuid: The UUID of the collection
            entity_type: The entity type (if applicable)

        Returns:
            Dict[str, Any]: The created workspace item
        """
        params = [("owningCollection", collection_uuid)] + ([("entityType", entity_type)] if entity_type else [])
        ws = self._request("POST", self._url(self.WS), f"Start submission in {collection_uuid}",
                           expected=(200, 201), headers=self._headers(), params=params).json()
        logger.info("Started workspace item %s in collection %s", ws.get("id"), collection_uuid)
        return ws

    def create_from_external_source(self, collection_uuid: str, entry_href: str) -> Dict[str, Any]:
        """
        Start a submission pre-filled from an external entry, e.g.
        <api>/integration/externalsources/orcid/entryValues/0000-0002-4271-0436

        Parameters:
            collection_uuid: The UUID of the collection
            entry_href: The href of the external entry

        Returns:
            Dict[str, Any]: The created workspace item
        """
        return self._request("POST", self._url(self.WS), "Create workspace item from external source",
                             expected=(200, 201), headers=self._headers("text/uri-list"),
                             params={"owningCollection": collection_uuid},
                             data=self.resolve_href(entry_href)).json()

    def create_from_files(self, sources: Sequence[Any], collection_uuid: Optional[str] = None,
                          timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        Start a submission from file(s) (BibTeX, RIS, PubMed XML, ...): the first
        parseable file pre-fills the metadata, all files are attached. Without a
        collection, DSpace uses the first one the user may submit to.

        Parameters:
            sources: A sequence of file sources (file paths or tuples of name and content)
            collection_uuid: The UUID of the collection (optional)
            timeout: Request timeout in seconds (optional)

        Returns:
            List[Dict[str, Any]]: The created workspace items
        """
        from .bundles import BundleManager
        files = []
        for source in sources:
            name, content = BundleManager._read_source(*(source if isinstance(source, tuple) else (source, None)))
            files.append(("file", (name, content, "application/octet-stream")))
        if not files:
            raise ValueError("No files given")
        params = {"owningCollection": collection_uuid} if collection_uuid else None
        data = self._request("POST", self._url(self.WS), "Create workspace item from files",
                             expected=(200, 201), headers=self._headers(content_type=None), params=params,
                             files=files, timeout=timeout or 300).json()
        embedded = (data.get("_embedded") or {}).get("workspaceitems")
        return embedded if isinstance(embedded, list) else [data]

    # ------------------------------------------------------------------
    # Generic PATCH
    # ------------------------------------------------------------------

    def apply_patch(self, ws_id, operations: Union[MetadataPatch, Iterable[Dict[str, Any]]]) -> Dict[str, Any]:
        """PATCH the workspace item; a 422 raises SubmissionError with the item's validation errors.
        
        Parameters:
            ws_id: The ID of the workspace item
            operations: The patch operations to apply

        Returns:
            Dict[str, Any]: The updated workspace item

        Raises:
            SubmissionError: If the patch is rejected with validation errors
        """
        ops = as_operations(operations)
        if not ops:
            raise ValueError("Empty patch")
        try:
            return self._request("PATCH", self._ws_url(ws_id), f"Patch workspace item {ws_id}",
                                 headers=self._headers("application/json-patch+json"), json=ops).json()
        except DSpaceError as exc:
            if getattr(exc, "status_code", None) != 422:
                raise
            raise SubmissionError(f"Workspace item {ws_id} rejected the patch: {exc.response_text or exc}",
                                  errors=self._errors_from(exc.response_text), status_code=422,
                                  response_text=exc.response_text) from exc

    @staticmethod
    def _errors_from(text) -> List[Dict[str, Any]]:
        """Extracts validation errors from the response text.

        Parameters:
            text: The response text containing validation errors

        Returns:
            List[Dict[str, Any]]: A list of validation errors
        """
        try:
            data = json.loads(text or "")
        except ValueError:
            return []
        return data.get("errors", []) if isinstance(data, dict) else []

    @staticmethod
    def validation_errors(ws: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extracts validation errors from a workspace item.

        Parameters:
            ws: The workspace item containing validation errors

        Returns:
            List[Dict[str, Any]]: A list of validation errors in the format
                [{"message": "error.validation.required", "paths": ["/sections/traditionalpageone/dc.title"]}, ...]
        """
        return list(ws.get("errors") or [])

    # ------------------------------------------------------------------
    # Submission configuration
    # ------------------------------------------------------------------

    def get_submission_definitions(self) -> List[Dict[str, Any]]:
        """Retrieves all submission definitions.

        Returns:
            List[Dict[str, Any]]: A list of submission definitions
        """
        return list(self._iter_embedded(self._url(CONFIG, "/submissiondefinitions"), "submissiondefinitions",
                                        "List submission definitions"))

    def get_submission_definition(self, name: str) -> Dict[str, Any]:
        """Retrieves a submission definition by name.

        Parameters:
            name: The name of the submission definition

        Returns:
            Dict[str, Any]: The submission definition
        """
        return self._get_json(self._url(CONFIG, f"/submissiondefinitions/{name}"), f"Get submission definition {name}")

    def get_definition_for_collection(self, collection_uuid: str) -> Dict[str, Any]:
        return self._get_json(self._url(CONFIG, "/submissiondefinitions/search/findByCollection"),
                              f"Find submission definition of {collection_uuid}", params={"uuid": collection_uuid})

    def get_definition_sections(self, definition_name: str) -> List[Dict[str, Any]]:
        """
        Sections of a definition in order: [{"id", "header", "mandatory", "sectionType", "scope", ...}]

        Read from the definition itself (DSpace embeds them): the
        .../submissiondefinitions/{name}/sections sub-resource answers
        204 No Content on DSpace 8-10.

        Parameters:
            definition_name: The name of the submission definition

        Returns:
            List[Dict[str, Any]]: A list of sections in the submission definition
        """
        if definition_name not in self._definition_cache:
            definition = self._get_json(self._url(CONFIG, f"/submissiondefinitions/{definition_name}"),
                                        f"Get submission definition {definition_name}",
                                        embed={"sections": 500})
            sections = _embedded_list((definition.get("_embedded") or {}).get("sections"), "sections")
            if sections is None:
                url = self._url(CONFIG, f"/submissiondefinitions/{definition_name}/sections")
                page = self._get_optional(url, f"Sections of {definition_name}")
                sections = _embedded_list(page, "sections", "submissionsections") or []
            self._definition_cache[definition_name] = sections
        return self._definition_cache[definition_name]

    def get_submission_section(self, name: str) -> Dict[str, Any]:
        """Retrieves a submission section by name.

        Parameters:
            name: The name of the submission section

        Returns:
            Dict[str, Any]: The submission section
        """
        return self._get_json(self._url(CONFIG, f"/submissionsections/{name}"), f"Get submission section {name}")

    def get_submission_form(self, name: str) -> Dict[str, Any]:
        """Retrieves a submission form by name.

        Parameters:
            name: The name of the submission form

        Returns:
            Dict[str, Any]: The submission form
        """
        return self._get_json(self._url(CONFIG, f"/submissionforms/{name}"), f"Get submission form {name}")

    def get_submission_upload(self, name: str) -> Dict[str, Any]:
        """Upload section config: accessConditionOptions, maxSize, required, ..

        Parameters:
            name: The name of the submission upload section

        Returns:
            Dict[str, Any]: The submission upload section configuration
        """
        return self._get_json(self._url(CONFIG, f"/submissionuploads/{name}"), f"Get submission upload {name}")

    def get_access_options(self, name: str = "default") -> Dict[str, Any]:
        """Retrieves item-level access conditions offered by an 'access' section.

        Parameters:
            name: The name of the access options section (default is "default")

        Returns:
            Dict[str, Any]: The access options
        """
        return self._get_json(self._url(CONFIG, f"/submissionaccessoptions/{name}"), f"Get access options {name}")

    def get_cc_licenses(self) -> List[Dict[str, Any]]:
        """Retrieves all Creative Commons licenses.

        Returns:
            List[Dict[str, Any]]: A list of CC licenses
        """
        return list(self._iter_embedded(self._url(CONFIG, "/submissioncclicenses"), "submissioncclicenses",
                                        "List CC licenses"))

    def get_cc_license(self, name: str) -> Dict[str, Any]:
        """Retrieves a specific Creative Commons license by name.

        Parameters:
            name: The name of the CC license

        Returns:
            Dict[str, Any]: The CC license
        """
        return self._get_json(self._url(CONFIG, f"/submissioncclicenses/{name}"), f"Get CC license {name}")

    def get_cc_license_url(self, license_name: str, answers: Optional[Dict[str, str]] = None) -> str:
        """Retrieves the license URI for a CC license with optional answers.

        Parameters:
            license_name: The name of the CC license
            answers: Optional dictionary of answers to license questions

        Returns:
            str: The license URI

        The contract spells the resource both "submissioncclicenseUrls" and
        "submissioncclicenseurls"; both are tried.
        """
        params = [("license", license_name)] + [(f"answer_{k}", v) for k, v in (answers or {}).items()]
        last = None
        for resource in ("submissioncclicenseUrls", "submissioncclicenseurls"):
            try:
                url = self._url(CONFIG, f"/{resource}/search/rightsByQuestions")
                return self._get_json(url, f"CC license URL for {license_name}", params=params)["url"]
            except DSpaceError as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise
                last = exc
        raise last

    # ------------------------------------------------------------------
    # Sections of a workspace item
    # ------------------------------------------------------------------

    def section_types(self, ws_id) -> Dict[str, str]:
        """Retrieves the section types for the item's submission definition.

        Parameters:
            ws_id: The workspace item ID

        Returns:
            Dict[str, str]: A dictionary mapping section names to section types, in definition order
        """
        definition = self.get_definition_of(ws_id)
        return {s["id"]: s.get("sectionType") for s in self.get_definition_sections(definition["name"])}

    def section_name(self, ws_id, section_type: str) -> str:
        """Name of the (first) section of a type, e.g. 'upload' -> 'upload' or 'uploadThesis'.
        
        Parameters:
            ws_id: The workspace item ID
            section_type: The type of the section to find

        Returns:
            str: The name of the first section of the given type

        Raises:
            ValueError: If no section of the given type is found
        """
        for name, kind in self.section_types(ws_id).items():
            if kind == section_type:
                return name
        raise ValueError(f"Workspace item {ws_id} has no {section_type!r} section in its submission definition")

    def form_fields(self, section_name: str) -> List[str]:
        """Retrieves the metadata fields offered by a submission-form section (all selectableMetadata of all rows).

        Parameters:
            section_name: The name of the submission form section

        Returns:
            List[str]: A list of metadata field names
        """
        if section_name not in self._form_fields_cache:
            section = self.get_submission_section(section_name)
            href = ((section.get("_links") or {}).get("config") or {}).get("href")
            form = self.get_by_href(href) if href else self.get_submission_form(section_name)
            fields: List[str] = []
            for row in form.get("rows") or []:
                for field in row.get("fields") or []:
                    for selectable in field.get("selectableMetadata") or []:
                        name = selectable.get("metadata")
                        if name and name not in fields:
                            fields.append(name)
            self._form_fields_cache[section_name] = fields
        return self._form_fields_cache[section_name]

    def route_metadata(self, ws_id, metadata: Dict[str, Any], ws: Optional[Dict[str, Any]] = None
                       ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
        """Routes metadata fields to the submission-form sections that offer them.

        Parameters:
            ws_id: The workspace item ID
            metadata: A dictionary of metadata fields and their values
            ws: Optional workspace item dictionary (retrieved if not provided)

        Returns:
            Tuple[Dict[str, Dict[str, Any]], List[str]]: A tuple containing:
                - A dictionary mapping section names to dictionaries of field-value pairs
                - A list of fields that could not be routed to any section

        Only sections active in the current submission phase are considered.
        """
        ws = ws or self.get(ws_id)
        active = set((ws.get("sections") or {}).keys())
        forms = [name for name, kind in self.section_types(ws_id).items()
                 if kind == "submission-form" and (not active or name in active)]
        routed: Dict[str, Dict[str, Any]] = {}
        unroutable: List[str] = []
        for field, values in metadata.items():
            section = next((f for f in forms if field in self.form_fields(f)), None)
            if section is None:
                unroutable.append(field)
            else:
                routed.setdefault(section, {})[field] = values
        return routed, unroutable

    def fill_metadata(self, ws_id, metadata: Dict[str, Any], strict: bool = True) -> Dict[str, Any]:
        """
        Sets metadata fields on a submission, each field in the form section
        that offers it (replacing existing values). Fields no form offers raise
        ValueError (strict) or are skipped with a warning.

        Parameters:
            ws_id: The workspace item ID
            metadata: A dictionary of metadata fields and their values
            strict: If True, raise an error for unroutable fields; otherwise, log a warning

        Returns:
            Dict[str, Any]: The updated workspace item
        """
        ws = self.get(ws_id)
        routed, unroutable = self.route_metadata(ws_id, metadata, ws)
        if unroutable:
            message = f"No submission form of workspace item {ws_id} offers {unroutable}"
            if strict:
                raise ValueError(message + " - add them to the form, or set them on the item after deposit")
            logger.warning("%s - skipped", message)
        patch = MetadataPatch()
        for section, fields in routed.items():
            section_patch = MetadataPatch.for_section(section)
            for field, values in fields.items():
                section_patch.set(field, values)
            patch.extend(section_patch)
        return self.apply_patch(ws_id, patch) if len(patch) else ws

    def patch_section_metadata(self, ws_id, section: str, patch: Union[MetadataPatch, Dict[str, Any]]
                               ) -> Dict[str, Any]:
        """Applies a metadata patch to a specific section of the workspace item.

        Parameters:
            ws_id: The workspace item ID
            section: The name of the section to patch
            patch: Either a MetadataPatch object for the section or a dictionary of field-value pairs (each replaces the field)

        Returns:
            Dict[str, Any]: The updated workspace item
        """
        if isinstance(patch, dict):
            built = MetadataPatch.for_section(section)
            for field, values in patch.items():
                built.set(field, values)
            patch = built
        return self.apply_patch(ws_id, patch)

    # ------------------------------------------------------------------
    # upload section
    # ------------------------------------------------------------------

    def upload_file(self, ws_id, source: Any, name: Optional[str] = None, mime_type: Optional[str] = None,
                    timeout: Optional[Union[float, Tuple[float, float]]] = None, progress=None,
                    chunk_size: Optional[int] = None) -> Dict[str, Any]:
        """
        Uploads a file to the workspace item (multipart POST); returns the updated workspace item.

        Parameters:
            ws_id: The workspace item ID
            source: The file source (path, file-like object, etc.)
            name: Optional name of the file
            mime_type: Optional MIME type of the file
            timeout: Optional timeout for the upload
            progress: Optional progress callback (bytes_sent, total)
            chunk_size: Optional chunk size for the upload

        Returns:
            Dict[str, Any]: The updated workspace item

        Notes:
            The upload is streamed like BundleManager.upload_bitstream: any size, constant memory,
            read timeout scaled to the file size, progress(bytes_sent, total) per chunk.
        """
        import mimetypes
        from .bundles import BundleManager, _MultipartStream, _OpenedSource
        with _OpenedSource(source, name) as src:
            mime_type = mime_type or mimetypes.guess_type(src.name)[0] or "application/octet-stream"
            body = _MultipartStream(src.file, src.size, src.name, mime_type, None,
                                    chunk_size=chunk_size or BundleManager.UPLOAD_CHUNK_SIZE, progress=progress)
            headers = self._headers(content_type=None)
            headers["Content-Type"] = body.content_type
            headers["Content-Length"] = str(len(body))
            logger.info("Uploading %s (%d bytes) to workspace item %s", src.name, src.size, ws_id)
            return self._request("POST", self._ws_url(ws_id), f"Upload {src.name} to workspace item {ws_id}",
                                 expected=(200, 201), headers=headers, data=body,
                                 timeout=timeout or BundleManager(self.auth)._upload_timeout(src.size)).json()

    @staticmethod
    def uploaded_files(ws: Dict[str, Any]) -> List[Tuple[str, int, Dict[str, Any]]]:
        """Returns a list of uploaded files for a workspace item.

        Each item in the list is a tuple (section, index, file) where:
            section: The section name in the workspace item
            index: The index of the file within the section
            file: The file dictionary from the workspace item JSON

        Parameters:
            ws: The workspace item JSON dictionary

        Returns:
            List[Tuple[str, int, Dict[str, Any]]]: A list of uploaded files with their section and index.
        """
        result = []
        for section, data in (ws.get("sections") or {}).items():
            if isinstance(data, dict) and isinstance(data.get("files"), list):
                result.extend((section, i, f) for i, f in enumerate(data["files"]))
        return result

    @classmethod
    def find_file(cls, ws: Dict[str, Any], name_or_uuid: str) -> Tuple[str, int, Dict[str, Any]]:
        """Locate an uploaded file by bitstream uuid or title (dc.title).

        Parameters:
            ws: The workspace item JSON dictionary
            name_or_uuid: The bitstream UUID or title to search for

        Returns:
            Tuple[str, int, Dict[str, Any]]: A tuple containing the section name, file index, and file dictionary

        Raises:
            LookupError: If no matching file is found
        """
        for section, index, file in cls.uploaded_files(ws):
            titles = [v.get("value") for v in (file.get("metadata") or {}).get("dc.title") or []]
            if file.get("uuid") == name_or_uuid or name_or_uuid in titles:
                return section, index, file
        raise LookupError(f"No uploaded file {name_or_uuid!r} in workspace item {ws.get('id')}")

    def _upload_section(self, ws_id, section: Optional[str]) -> str:
        """Determine the upload section for a workspace item.

        Parameters:
            ws_id: The workspace item ID
            section: The section name, if specified

        Returns:
            str: The section name to use for uploads
        """
        return section or self.section_name(ws_id, "upload")

    def patch_file_metadata(self, ws_id, file_index: int, patch: Union[MetadataPatch, Dict[str, Any]],
                            section: Optional[str] = None) -> Dict[str, Any]:
        """MetadataPatch.for_file(section, i)... or {field: values} for the uploaded file at file_index.
        
        Parameters:
            ws_id: The workspace item ID
            file_index: The index of the uploaded file to patch
            patch: The metadata patch to apply (MetadataPatch or dictionary)
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after applying the patch 
        """
        section = self._upload_section(ws_id, section)
        if isinstance(patch, dict):
            built = MetadataPatch.for_file(section, file_index)
            for field, values in patch.items():
                built.set(field, values)
            patch = built
        return self.apply_patch(ws_id, patch)

    @staticmethod
    def access_condition(name: str, start_date=None, end_date=None) -> Dict[str, Any]:
        """Create an access condition dictionary.

        Parameters:
            name: The name of the access condition (e.g., "embargo")
            start_date: The start date of the access condition (optional)
            end_date: The end date of the access condition (optional)

        Returns:
            Dict[str, Any]: The access condition dictionary
        """
        from .permissions import _fmt_date
        condition = {"name": name}
        if start_date:
            condition["startDate"] = _fmt_date(start_date)
        if end_date:
            condition["endDate"] = _fmt_date(end_date)
        return condition

    def add_file_access_condition(self, ws_id, file_index: int, name: str, start_date=None, end_date=None,
                                  section: Optional[str] = None) -> Dict[str, Any]:
        """Add an access condition to a file in the workspace item.

        Parameters:
            ws_id: The workspace item ID
            file_index: The index of the uploaded file
            name: The name of the access condition (e.g., "embargo")
            start_date: The start date of the access condition (optional)
            end_date: The end date of the access condition (optional)
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after adding the access condition

        Example:
            add_file_access_condition(ws_id, 0, "embargo", start_date="2027-01-01")
        """
        section = self._upload_section(ws_id, section)
        return self.apply_patch(ws_id, [{"op": "add", "path": f"/sections/{section}/files/{file_index}/accessConditions/-",
                                         "value": self.access_condition(name, start_date, end_date)}])

    def set_file_access_conditions(self, ws_id, file_index: int, conditions: Sequence[Dict[str, Any]],
                                   section: Optional[str] = None) -> Dict[str, Any]:
        """Replace all access conditions of one file.

        Parameters:
            ws_id: The workspace item ID
            file_index: The index of the uploaded file
            conditions: A sequence of access condition dictionaries
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after setting the access conditions
        """
        section = self._upload_section(ws_id, section)
        value = [self.access_condition(c["name"], c.get("startDate"), c.get("endDate")) for c in conditions]
        return self.apply_patch(ws_id, [{"op": "add", "path": f"/sections/{section}/files/{file_index}/accessConditions",
                                         "value": value}])

    def remove_file_access_condition(self, ws_id, file_index: int, index: Optional[int] = None,
                                     section: Optional[str] = None) -> Dict[str, Any]:
        """Remove one access condition, or all of them (index=None)."""
        section = self._upload_section(ws_id, section)
        path = f"/sections/{section}/files/{file_index}/accessConditions" + ("" if index is None else f"/{index}")
        return self.apply_patch(ws_id, [{"op": "remove", "path": path}])

    def remove_file(self, ws_id, file_index: int, section: Optional[str] = None) -> Dict[str, Any]:
        """Remove an uploaded file from the workspace item.

        Parameters:
            ws_id: The workspace item ID
            file_index: The index of the uploaded file to remove
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after removing the file
        """
        section = self._upload_section(ws_id, section)
        return self.apply_patch(ws_id, [{"op": "remove", "path": f"/sections/{section}/files/{file_index}"}])

    def move_file(self, ws_id, from_index: int, to_index: int, section: Optional[str] = None) -> Dict[str, Any]:
        section = self._upload_section(ws_id, section)
        return self.apply_patch(ws_id, [{"op": "move", "from": f"/sections/{section}/files/{from_index}",
                                         "path": f"/sections/{section}/files/{to_index}"}])

    def set_primary_file(self, ws_id, bitstream_uuid: str, section: Optional[str] = None) -> Dict[str, Any]:
        """Mark an uploaded file as primary ('add' both sets and changes it).

        Parameters:
            ws_id: The workspace item ID
            bitstream_uuid: The UUID of the bitstream to set as primary
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after setting the primary file
        """
        section = self._upload_section(ws_id, section)
        return self.apply_patch(ws_id, [{"op": "add", "path": f"/sections/{section}/primary", "value": bitstream_uuid}])

    def clear_primary_file(self, ws_id, section: Optional[str] = None) -> Dict[str, Any]:
        """Clear the primary file of the workspace item.

        Parameters:
            ws_id: The workspace item ID
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after clearing the primary file
        """
        section = self._upload_section(ws_id, section)
        return self.apply_patch(ws_id, [{"op": "remove", "path": f"/sections/{section}/primary"}])

    # ------------------------------------------------------------------
    # license / access / cclicense / sherpa sections
    # ------------------------------------------------------------------

    def grant_license(self, ws_id, granted: bool = True, section: Optional[str] = None) -> Dict[str, Any]:
        """Accept (or with granted=False reject) the deposit license.

        Parameters:
            ws_id: The workspace item ID
            granted: Whether the license is granted (True) or rejected (False)
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after granting or rejecting the license
        """
        section = section or self.section_name(ws_id, "license")
        return self.apply_patch(ws_id, [{"op": "add", "path": f"/sections/{section}/granted", "value": bool(granted)}])

    def add_access_condition(self, ws_id, name: str, start_date=None, end_date=None,
                             section: Optional[str] = None) -> Dict[str, Any]:
        """Item-level access condition (access section); options: get_access_options().
        
        Parameters:
            ws_id: The workspace item ID
            name: The name of the access condition (e.g., "embargo")
            start_date: The start date of the access condition (optional)
            end_date: The end date of the access condition (optional)
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after adding the access condition
        """
        section = section or self.section_name(ws_id, "access")
        return self.apply_patch(ws_id, [{"op": "add", "path": f"/sections/{section}/accessConditions/-",
                                         "value": self.access_condition(name, start_date, end_date)}])

    def set_access_conditions(self, ws_id, conditions: Sequence[Dict[str, Any]],
                              section: Optional[str] = None) -> Dict[str, Any]:
        """Replace all access conditions of the workspace item.

        Parameters:
            ws_id: The workspace item ID
            conditions: A sequence of access condition dictionaries
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after setting the access conditions
        """
        section = section or self.section_name(ws_id, "access")
        value = [self.access_condition(c["name"], c.get("startDate"), c.get("endDate")) for c in conditions]
        return self.apply_patch(ws_id, [{"op": "add", "path": f"/sections/{section}/accessConditions", "value": value}])

    def remove_access_condition(self, ws_id, index: Optional[int] = None, section: Optional[str] = None
                                ) -> Dict[str, Any]:
        """Remove an access condition from the workspace item.

        Parameters:
            ws_id: The workspace item ID
            index: The index of the access condition to remove (optional)
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after removing the access condition
        """
        section = section or self.section_name(ws_id, "access")
        path = f"/sections/{section}/accessConditions" + ("" if index is None else f"/{index}")
        return self.apply_patch(ws_id, [{"op": "remove", "path": path}])

    def set_discoverable(self, ws_id, discoverable: bool, section: Optional[str] = None) -> Dict[str, Any]:
        section = section or self.section_name(ws_id, "access")
        return self.apply_patch(ws_id, [{"op": "replace", "path": f"/sections/{section}/discoverable",
                                         "value": bool(discoverable)}])

    def set_cc_license(self, ws_id, license_uri: str, section: Optional[str] = None) -> Dict[str, Any]:
        """Apply a Creative Commons license by URI (see get_cc_license_url).

        Parameters:
            ws_id: The workspace item ID
            license_uri: The URI of the Creative Commons license
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after setting the CC license
        """
        section = section or self.section_name(ws_id, "cclicense")
        return self.apply_patch(ws_id, [{"op": "add", "path": f"/sections/{section}/uri", "value": license_uri}])

    def remove_cc_license(self, ws_id, section: Optional[str] = None) -> Dict[str, Any]:
        """Remove the Creative Commons license from the workspace item.

        Parameters:
            ws_id: The workspace item ID
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after removing the CC license
        """
        section = section or self.section_name(ws_id, "cclicense")
        return self.apply_patch(ws_id, [{"op": "remove", "path": f"/sections/{section}/uri"}])

    def refresh_sherpa_policies(self, ws_id, section: Optional[str] = None) -> Dict[str, Any]:
        """Drop the cached Sherpa data so it is fetched again.

        Parameters:
            ws_id: The workspace item ID
            section: The section name, if specified

        Returns:
            Dict[str, Any]: The updated workspace item after refreshing Sherpa policies
        """
        section = section or self.section_name(ws_id, "sherpaPolicies")
        return self.apply_patch(ws_id, [{"op": "remove", "path": f"/sections/{section}/retrievalTime"}])

    # ------------------------------------------------------------------
    # Deposit
    # ------------------------------------------------------------------

    def deposit(self, ws_id) -> Dict[str, Any]:
        """
        Send the submission into workflow (or straight to the archive when the
        collection has no workflow). Returns the workflow item JSON ({} if the
        server sent no body). Validation problems raise SubmissionError with
        the workspace item's errors.

        Parameters:
            ws_id: The workspace item ID

        Returns:
            Dict[str, Any]: The workflow item JSON after deposit ({} if the server sent no body)
        """
        url = self._url(Endpoints.WORKFLOW_ITEMS)
        try:
            response = self._request("POST", url, f"Deposit workspace item {ws_id}", expected=(200, 201),
                                     headers=self._headers("text/uri-list"), data=self._ws_url(ws_id))
        except DSpaceError as exc:
            if getattr(exc, "status_code", None) != 422:
                raise
            ws = self.get(ws_id)
            raise SubmissionError(f"Workspace item {ws_id} is not complete", errors=self.validation_errors(ws),
                                  workspace_item=ws, status_code=422, response_text=exc.response_text) from exc
        logger.info("Deposited workspace item %s", ws_id)
        return self._json_or_none(response) or {}

    # ------------------------------------------------------------------
    # One call
    # ------------------------------------------------------------------

    def submit(self, collection_uuid: str, metadata: Dict[str, Any], files: Sequence[Any] = (),
               file_access_conditions: Optional[Sequence[Dict[str, Any]]] = None,
               access_conditions: Optional[Sequence[Dict[str, Any]]] = None,
               grant_license: bool = True, deposit: bool = True, entity_type: Optional[str] = None,
               strict_metadata: bool = True, delete_on_error: bool = False) -> Dict[str, Any]:
        """
        Complete submission in one call:

            result = ws.submit(coll, {"dc.title": "Thesis", "dc.contributor.author": ["Doe, Jane"],
                                      "dc.date.issued": "2026"},
                               files=["thesis.pdf"],
                               file_access_conditions=[{"name": "embargo", "startDate": "2027-01-01"}])
            result["item_uuid"]

        files: paths, bytes with a name as (bytes, "name.pdf"), or binary file objects.
        file_access_conditions apply to every uploaded file; access_conditions
        need an 'access' section. The license is granted if the definition has
        a license section. With deposit=False the workspace item is left for review.

        Returns {"workspace_item", "workflow_item", "item_uuid", "errors"}.
        Validation errors raise SubmissionError (deposit=True) - the workspace
        item is kept for inspection unless delete_on_error=True.

        Parameters:
            collection_uuid: The UUID of the collection to submit to
            metadata: A dictionary of metadata for the submission
            files: A sequence of file paths, bytes with a name, or binary file objects
            file_access_conditions: A sequence of access condition dictionaries for the files
            access_conditions: A sequence of access condition dictionaries for the workspace item
            grant_license: Whether to grant the license if applicable
            deposit: Whether to deposit the item immediately
            entity_type: The entity type of the submission, if specified
            strict_metadata: Whether to enforce strict metadata validation
            delete_on_error: Whether to delete the workspace item on error

        Returns:
            Dict[str, Any]: A dictionary containing the workspace item, workflow item, item UUID, and any validation errors
        """
        ws = self.start_submission(collection_uuid, entity_type)
        ws_id = ws["id"]
        try:
            ws = self.fill_metadata(ws_id, metadata, strict=strict_metadata)
            types = self.section_types(ws_id)
            for source in files:
                ws = self.upload_file(ws_id, *(source if isinstance(source, tuple) else (source,)))
            if file_access_conditions:
                for section, index, _file in self.uploaded_files(ws):
                    ws = self.set_file_access_conditions(ws_id, index, file_access_conditions, section=section)
            if access_conditions:
                ws = self.set_access_conditions(ws_id, access_conditions)
            if grant_license and "license" in types.values():
                ws = self.grant_license(ws_id)
            item_uuid = self.get_item_of(ws_id).get("uuid")
            result = {"workspace_item": ws, "workflow_item": None, "item_uuid": item_uuid,
                      "errors": self.validation_errors(ws)}
            if deposit:
                if result["errors"]:
                    raise SubmissionError(f"Workspace item {ws_id} is not complete", errors=result["errors"],
                                          workspace_item=ws)
                result["workflow_item"] = self.deposit(ws_id)
            return result
        except Exception:
            if delete_on_error:
                try:
                    self.delete(ws_id)
                except DSpaceError:  # pragma: no cover - best effort
                    logger.warning("Could not delete workspace item %s after a failed submission", ws_id)
            raise
