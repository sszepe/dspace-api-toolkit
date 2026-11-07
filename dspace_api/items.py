# -*- coding: UTF-8 -*-
"""
Item management for DSpace API - handles archived items
"""

import logging
import json
from typing import Dict, List, Optional
from .config import Endpoints
from .projection import view_params
from .exceptions import handle_response_error, BadRequestError

# Logger for the module
logger = logging.getLogger(__name__)


class ItemManager:
    """Manages DSpace archived items"""

    def __init__(self, auth):
        """
        Initialize the ItemManager with authentication and configuration.

        Parameters:
            auth: Authentication object containing session and configuration.
        """
        self.auth = auth
        self.session = auth.session
        self.config = auth.config

    def get_items(self, page=0, size=20, sort=None, all_languages=None, projection=None, embed=None):
        """
        Get a list of items from DSpace

        Parameters:
            page: Page number (0-based)
            size: Number of items per page
            sort: Sort criteria
            all_languages: projection=allLanguages for this call (None = client setting)
            projection / embed: shape the response, e.g. embed="owningCollection" (see projection.py)

        Returns:
            dict: API response containing items
        """
        logger.info("Getting items (page=%d, size=%d)", page, size)

        url = self.config.dspace_url + Endpoints.ITEMS
        params = {"page": page, "size": size}
        if sort:
            params["sort"] = sort
        params = view_params(params, self.config, all_languages, projection, embed)

        try:
            response = self.session.get(
                url,
                headers=self.config.headers,
                params=params,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Successfully retrieved items")
                return response.json()
            else:
                handle_response_error(response, "Get items")

        except Exception as e:
            logger.error("Failed to get items: %s", e)
            raise

    def get_item(self, item_uuid, all_languages=None, projection=None, embed=None):
        """
        Get a specific item by UUID

        Parameters:
            item_uuid: UUID of the item
            all_languages: projection=allLanguages for this call (None = client setting)
            projection / embed: shape the response, e.g. embed=["bundles/bitstreams", "owningCollection"]
                (see projection.py; read results with projection.get_embedded)

        Returns:
            dict: Item data
        """
        logger.info("Getting item %s", item_uuid)

        url = f"{self.config.dspace_url}{Endpoints.ITEMS}/{item_uuid}"
        params = view_params(None, self.config, all_languages, projection, embed)

        try:
            response = self.session.get(
                url,
                headers=self.config.headers,
                params=params,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Successfully retrieved item %s", item_uuid)
                return response.json()
            else:
                handle_response_error(response, f"Get item {item_uuid}")

        except Exception as e:
            logger.error("Failed to get item %s: %s", item_uuid, e)
            raise

    def create_item(self, collection_uuid, metadata, entity_type,
                   in_archive=True, discoverable=True, withdrawn=False):
        """
        Create an archived item directly in a collection

        Parameters:
            collection_uuid: UUID of the owning collection
            metadata: Dictionary of metadata fields
            entity_type: Type of entity (e.g., "Publication"); None for a plain item
            in_archive: Whether item should be archived immediately
            discoverable: Whether item should be discoverable
            withdrawn: Whether item is withdrawn

        Returns:
            dict: Created item data
        """
        logger.info("Creating item in collection %s", collection_uuid)

        # Prepare item data
        item_data = {
            "metadata": self._format_metadata(metadata),
            "inArchive": in_archive,
            "discoverable": discoverable,
            "withdrawn": withdrawn,
            "type": "item"
        }

        # entity_type=None: plain item (repositories without configurable entities)
        if entity_type:
            item_data["entityType"] = entity_type
            # Add entity type to metadata if not present
            if "dspace.entity.type" not in item_data["metadata"]:
                item_data["metadata"]["dspace.entity.type"] = [{"value": entity_type}]

        url = f"{self.config.dspace_url}{Endpoints.ITEMS}?owningCollection={collection_uuid}"

        try:
            response = self.session.post(
                url,
                headers=self.config.headers,
                data=json.dumps(item_data),
                timeout=self.config.timeout
            )

            if response.status_code == 201:
                logger.info("Item created successfully")
                return response.json()
            else:
                handle_response_error(response, "Create item")

        except Exception as e:
            logger.error("Failed to create item: %s", e)
            raise

    def update_item(self, item_uuid, operation, metadata):
        """
        Update item metadata

        Parameters:
            item_uuid: UUID of the item to update
            operation: JSON-Patch operation to perform (e.g., "add", "replace", "remove")
            metadata: Dictionary of metadata fields to update

        Returns:
            dict: Updated item data
        """
        logger.info("Updating item %s", item_uuid)

        # Create patch operations for metadata updates
        patch_operations = []

        for field, values in metadata.items():
            patch_operations.append({
                "op": operation, # add, replace, remove
                "path": f"/metadata/{field}",
                "value": self._format_metadata_field(values)
            })

        url = f"{self.config.dspace_url}{Endpoints.ITEMS}/{item_uuid}"

        try:
            response = self.session.patch(
                url,
                headers=self.config.headers,
                json=patch_operations,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Item %s updated successfully", item_uuid)
                return response.json()
            else:
                handle_response_error(response, f"Update item {item_uuid}")

        except Exception as e:
            logger.error("Failed to update item %s: %s", item_uuid, e)
            raise

    def upsert_metadata(self, item_uuid, metadata, existing_metadata=None):
        """
        Add-or-replace metadata fields on an already-archived item, choosing
        the correct JSON-Patch op per field automatically.

        Plain PATCH "replace" on a metadata path that doesn't exist yet on
        the item can be rejected by DSpace, and plain "add" on a path that
        already has values just appends (creating duplicates) instead of
        overwriting. This method looks at what the item currently has and
        picks "replace" for fields it already carries and "add" for fields
        that are genuinely new, so a single call is safe to use both for
        first-time enrichment and for repeat syncs.

        Parameters:
            item_uuid: UUID of the item to update
            metadata: dict of field -> value(s). Values may be plain
                strings, lists of strings, or lists of pre-formatted
                metadata-value dicts (e.g. to set authority/confidence for
                a controlled or linked field such as dc.relation.project).
            existing_metadata: optional dict as returned by item["metadata"]
                (field -> list of value dicts). If omitted, the item is
                fetched first so the add/replace decision can be made
                safely.

        Returns:
            dict: updated item data
        """
        if existing_metadata is None:
            # all languages: a field with values only in another language still exists
            current = self.get_item(item_uuid, all_languages=True)
            existing_metadata = current.get("metadata", {}) or {}

        logger.info("Upserting metadata on item %s (%d field(s))", item_uuid, len(metadata))

        patch_operations = []
        for field, values in metadata.items():
            op = "replace" if existing_metadata.get(field) else "add"
            patch_operations.append({
                "op": op,
                "path": f"/metadata/{field}",
                "value": self._format_metadata_field(values),
            })

        url = f"{self.config.dspace_url}{Endpoints.ITEMS}/{item_uuid}"

        try:
            response = self.session.patch(
                url,
                headers=self.config.headers,
                json=patch_operations,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Item %s upserted successfully", item_uuid)
                return response.json()
            else:
                handle_response_error(response, f"Upsert item {item_uuid}")

        except Exception as e:
            logger.error("Failed to upsert item %s: %s", item_uuid, e)
            raise


    # STT -------------------------------------------------------------------------------------------------------------------------

    def upsert_metadata_by_position(self, item_uuid, metadata):
        """
        IMPORTANT: This function is an adapted version of 'upsert_metadata' because as of 2026-07-29 there is an issue with patch when the field is a list.
                https://github.com/DSpace/DSpace/issues/12419
                https://github.com/DSpace/DSpace/pull/12428
                https://github.com/DSpace/DSpace/pull/12467

        Add-or-replace metadata fields on an already-archived item, choosing
        the correct JSON-Patch op per field automatically depending on metadata place-value.

        Plain PATCH "replace" on a metadata path that doesn't exist yet on
        the item can be rejected by DSpace, and plain "add" on a path that
        already has values just appends (creating duplicates) instead of
        overwriting. This method looks at what the item currently has and
        picks "replace" for fields it already carries and "add" for fields
        that are genuinely new, so a single call is safe to use both for
        first-time enrichment and for repeat syncs.

        Parameters:
            item_uuid: UUID of the item to update
            metadata: dict of field -> value(s). Values has to be a pre-formatted
                metadata-value dict (e.g. to set authority/confidence for
                a controlled or linked field such as dc.relation.project).

        Returns:
            dict: updated item data
        """

        # the item is fetched first so the add/replace decision can be made safely
        # existing_metadata is a dict as returned by item["metadata"] (field -> list of value dicts)
        # all languages: PATCH positions refer to the complete value list, not the
        # locale-filtered one, otherwise "place" would point at the wrong value
        current = self.get_item(item_uuid, all_languages=True)
        existing_metadata = current.get("metadata", {}) or {}

        logger.info("Upserting metadata on item %s (%d field(s))", item_uuid, len(metadata))

        patch_operations = []
        for field, values in metadata.items():
            metadata_position = values.get("place")

            if not isinstance(metadata_position, int):
                raise TypeError(f"Item={item_uuid}: place-value has to be a valid integer. place: {metadata_position}")

            if existing_metadata.get(field):
                # check place-value of metadata to see if we have an 'replace' or 'add' operation

                # get number of entries in existing metadata field
                num_entries_existing_metadata_field = len(existing_metadata.get(field))

                if metadata_position < num_entries_existing_metadata_field:
                    # replace at metadata_position

                    patch_operations.append({
                        "op": "replace",
                        "path": f"/metadata/{field}/{metadata_position}",
                        "value": values if isinstance(values,dict) else self._format_metadata_field(values),
                    })

                elif metadata_position == num_entries_existing_metadata_field:
                    # add at the end without changing existing data
                    patch_operations.append({
                        "op": "add",
                        "path": f"/metadata/{field}/-",
                        "value": values if isinstance(values,dict) else self._format_metadata_field(values),
                    })

                else:
                    raise IndexError(f"Item={item_uuid}: The place-value of metadata is too big to be added as new metadata. metadata_place={metadata_position} next_valid_place={num_entries_existing_metadata_field}")
            elif metadata_position == 0:
                # add new metadata field
                patch_operations.append({
                    "op": "add",
                    "path": f"/metadata/{field}",
                    "value": values if isinstance(values,dict) else self._format_metadata_field(values),
                })
            else:
                raise IndexError(f"Item={item_uuid}: The place-value of metadata has to be 0 as it would be the first entry for this new metadata field. metadata_place={metadata_position}")


        url = f"{self.config.dspace_url}{Endpoints.ITEMS}/{item_uuid}"

        try:
            response = self.session.patch(
                url,
                headers=self.config.headers,
                json=patch_operations,
                timeout=self.config.timeout
            )

            if response.status_code == 200:
                logger.info("Item %s upserted successfully", item_uuid)
                return response.json()
            else:
                handle_response_error(response, f"Upsert item {item_uuid}")

        except Exception as e:
            logger.error("Failed to upsert item %s: %s", item_uuid, e)
            raise


    def delete_item(self, item_uuid):
        """
        Delete an item

        Parameters:
            item_uuid: UUID of the item to delete

        Returns:
            bool: True if deletion was successful
        """
        logger.info("Deleting item %s", item_uuid)

        url = f"{self.config.dspace_url}{Endpoints.ITEMS}/{item_uuid}"

        try:
            response = self.session.delete(
                url,
                headers=self.config.headers,
                timeout=self.config.timeout
            )

            if response.status_code == 204:
                logger.info("Item %s deleted successfully", item_uuid)
                return True
            else:
                handle_response_error(response, f"Delete item {item_uuid}")

        except Exception as e:
            logger.error("Failed to delete item %s: %s", item_uuid, e)
            raise

    def change_owning_collection(self, item_uuid, new_collection_uuid, inherit_policies=False):
        """
        Change the owning collection of an item

        Parameters:
            item_uuid: UUID of the item
            new_collection_uuid: UUID of the new owning collection
            inherit_policies: Whether to inherit policies from new collection

        Returns:
            bool: True if successful
        """
        logger.info("Changing owning collection of item %s to %s", item_uuid, new_collection_uuid)

        url = f"{self.config.dspace_url}{Endpoints.ITEMS}/{item_uuid}/owningCollection"
        if inherit_policies:
            url += "?inheritPolicies=true"

        headers = {"Content-Type": "text/uri-list"}
        data = f"{self.config.dspace_url}/core/collections/{new_collection_uuid}"

        try:
            response = self.session.put(
                url,
                headers=headers,
                data=data,
                timeout=self.config.timeout
            )

            if response.status_code in (200, 204):
                logger.info("Owning collection updated successfully")
                return True
            else:
                handle_response_error(response, "Change owning collection")

        except Exception as e:
            logger.error("Failed to change owning collection: %s", e)
            raise

    def add_mapped_collection(self, item_uuid, collection_uuid):
        """
        Add a mapped collection to an item

        Parameters:
            item_uuid: UUID of the item
            collection_uuid: UUID of the collection to map

        Returns:
            bool: True if successful
        """
        logger.info("Mapping item %s to collection %s", item_uuid, collection_uuid)

        url = f"{self.config.dspace_url}{Endpoints.ITEMS}/{item_uuid}/mappedCollections"
        headers = {"Content-Type": "text/uri-list"}
        data = f"{self.config.dspace_url}/core/collections/{collection_uuid}"

        try:
            response = self.session.post(
                url,
                headers=headers,
                data=data,
                timeout=self.config.timeout
            )

            if response.status_code == 204:
                logger.info("Mapped collection added successfully")
                return True
            else:
                handle_response_error(response, "Add mapped collection")

        except Exception as e:
            logger.error("Failed to add mapped collection: %s", e)
            raise

    def remove_mapped_collection(self, item_uuid, collection_uuid):
        """
        Remove a mapped collection from an item

        Parameters:
            item_uuid: UUID of the item
            collection_uuid: UUID of the collection to unmap

        Returns:
            bool: True if successful
        """
        logger.info("Removing mapped collection %s from item %s", collection_uuid, item_uuid)

        url = f"{self.config.dspace_url}{Endpoints.ITEMS}/{item_uuid}/mappedCollections/{collection_uuid}"

        try:
            response = self.session.delete(
                url,
                headers=self.config.headers,
                timeout=self.config.timeout
            )

            if response.status_code == 204:
                logger.info("Mapped collection removed successfully")
                return True
            else:
                handle_response_error(response, "Remove mapped collection")

        except Exception as e:
            logger.error("Failed to remove mapped collection: %s", e)
            raise

    def _format_metadata(self, metadata_dict):
        """
        Format metadata dictionary for DSpace API

        Parameters:
            metadata_dict: Dictionary of metadata fields

        Returns:
            dict: Formatted metadata
        """
        formatted = {}

        for field, values in metadata_dict.items():
            formatted[field] = self._format_metadata_field(values)

        return formatted

    def _format_metadata_field(self, values):
        """
        Format metadata field values for DSpace API

        Parameters:
            values: String, list of strings, or list of dicts

        Returns:
            list: Formatted metadata values
        """
        if isinstance(values, str):
            return [{"value": values}]
        elif isinstance(values, list):
            formatted = []
            for value in values:
                if isinstance(value, str):
                    formatted.append({"value": value})
                elif isinstance(value, dict):
                    formatted.append(value)
                else:
                    formatted.append({"value": str(value)})
            return formatted
        else:
            return [{"value": str(values)}]
