# -*- coding: UTF-8 -*-
"""
Utility functions for DSpace API client
"""

import os
import json
import logging
import re
import math
from datetime import datetime
from typing import Dict, List, Optional, Union

# Logger for the module
logger = logging.getLogger(__name__)

def format_metadata_value(value, language=None, authority=None, confidence=-1, place=0):
    """
    Format a single metadata value for DSpace API

    Parameters:
        value: The metadata value
        language: Language code (e.g., "en", "de")
        authority: Authority value for controlled vocabularies
        confidence: Confidence level for authority values
        place: Position/order of the value

    Returns:
        dict: Formatted metadata value
    """
    metadata_value = {
        "value": str(value),
        "language": language,
        "authority": authority,
        "confidence": confidence,
        "place": place
    }

    # Remove None values to keep JSON clean
    return {k: v for k, v in metadata_value.items() if v is not None}

def format_metadata_field(values, default_language=None):
    """
    Format metadata field values for DSpace API

    Parameters:
        values: String, list of strings, or list of dicts
        default_language: Default language to use if not specified

    Returns:
        list: Formatted metadata values
    """
    if isinstance(values, str):
        return [format_metadata_value(values, language=default_language)]
    elif isinstance(values, list):
        formatted = []
        for i, value in enumerate(values):
            if isinstance(value, str):
                formatted.append(format_metadata_value(value, language=default_language, place=i))
            elif isinstance(value, dict):
                # Use provided dict, but ensure place is set if not provided
                if "place" not in value:
                    value["place"] = i
                formatted.append(value)
            else:
                formatted.append(format_metadata_value(str(value), language=default_language, place=i))
        return formatted
    else:
        return [format_metadata_value(str(values), language=default_language)]

def extract_uuid_from_url(url):
    """
    Extract UUID from DSpace URL

    Parameters:
        url: DSpace URL containing UUID

    Returns:
        str: Extracted UUID or None
    """
    if not url:
        return None

    # Split by '/' and find the last part that looks like a UUID
    parts = url.split('/')
    for part in reversed(parts):
        if len(part) == 36 and part.count('-') == 4:  # UUID format
            return part

    return None

def parse_handle(handle_or_url):
    """
    Parse DSpace handle from various formats

    Parameters:
        handle_or_url: Handle string or URL

    Returns:
        str: Clean handle (e.g., "123456789/123")
    """
    if not handle_or_url:
        return None

    # Remove common URL prefixes
    handle = handle_or_url.replace("http://hdl.handle.net/", "")
    handle = handle.replace("https://hdl.handle.net/", "")

    # Extract handle pattern (numbers/numbers)
    match = re.search(r'(\d+/\d+)', handle)
    return match.group(1) if match else handle

def validate_metadata_field(field_name, values):
    """
    Validate metadata field format

    Parameters:
        field_name: Name of the metadata field
        values: Field values to validate

    Returns:
        bool: True if valid

    Raises:
        ValueError: If validation fails
    """
    if not field_name:
        raise ValueError("Field name cannot be empty")

    if not values:
        raise ValueError(f"No values provided for field {field_name}")

    # Check if field follows Dublin Core pattern - must have at least 2 parts after "dc."
    if field_name.startswith("dc.") and field_name.count(".") < 2:
        raise ValueError(f"Invalid Dublin Core field format: {field_name}")

    return True

def sanitize_filename(filename):
    """
    Sanitize filename for safe file operations

    Parameters:
        filename: Original filename

    Returns:
        str: Sanitized filename
    """
    # Remove or replace invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip(' .')
    # Limit length
    if len(sanitized) > 255:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:255-len(ext)] + ext

    return sanitized

def create_patch_operation(operation, path, value=None, from_path=None):
    """
    Create a JSON Patch operation

    Parameters:
        operation: Operation type ("add", "remove", "replace", "move", "copy", "test")
        path: JSON Pointer path
        value: Value for add/replace operations
        from_path: Source path for move/copy operations

    Returns:
        dict: JSON Patch operation
    """
    patch_op = {"op": operation, "path": path}

    if operation in ["add", "replace", "test"] and value is not None:
        patch_op["value"] = value
    elif operation in ["move", "copy"] and from_path:
        patch_op["from"] = from_path

    return patch_op

def batch_metadata_operations(metadata_updates):
    """
    Create batch patch operations for metadata updates

    Parameters:
        metadata_updates: Dict of field -> values mappings

    Returns:
        list: List of JSON Patch operations
    """
    operations = []

    for field, values in metadata_updates.items():
        operations.append(create_patch_operation(
            "replace",
            f"/metadata/{field}",
            format_metadata_field(values)
        ))

    return operations

def log_api_call(method, url, status_code, response_time=None):
    """
    Log API call details

    Parameters:
        method: HTTP method
        url: Request URL
        status_code: Response status code
        response_time: Response time in seconds
    """
    log_msg = f"{method} {url} -> {status_code}"
    if response_time:
        log_msg += f" ({response_time:.2f}s)"

    if 200 <= status_code < 300:
        logger.info(log_msg)
    elif 400 <= status_code < 500:
        logger.warning(log_msg)
    else:
        logger.error(log_msg)

def format_file_size(size_bytes):
    """
    Format file size in human readable format

    Parameters:
        size_bytes: Size in bytes

    Returns:
        str: Formatted size (e.g., "1.5 MB")
    """
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"

def check_file_exists_and_readable(file_path):
    """
    Check if file exists and is readable

    Parameters:
        file_path: Path to the file

    Returns:
        bool: True if file exists and is readable

    Raises:
        FileNotFoundError: If file doesn't exist
        PermissionError: If file is not readable
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    if not os.access(file_path, os.R_OK):
        raise PermissionError(f"File not readable: {file_path}")

    return True

def get_file_info(file_path):
    """
    Get file information

            raise DSpaceError(f"Version {version.get('id')} has no workspace item (already in workflow?)")

    Parameters:
        metadata: Flat metadata dictionary
        entity_type: Entity type for the item

    Returns:
        dict: Metadata organized into sections
        file_path: Path to the file

    Returns:
        dict: File information
    """
    check_file_exists_and_readable(file_path)

    stat = os.stat(file_path)
    return {
        "path": file_path,
        "name": os.path.basename(file_path),
        "size": stat.st_size,
        "size_formatted": format_file_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime),
        "extension": os.path.splitext(file_path)[1].lower()
    }

def create_workspace_sections_from_metadata(metadata, entity_type="Publication"):
    """
    Convert flat metadata dictionary to workspace sections format

    Parameters:
        metadata: Flat metadata dictionary
        entity_type: Entity type for the item

    Returns:
        dict: Metadata organized into sections
    """
    sections = {}

    # Define section mappings
    section_mappings = {
        "publication": [
            "dc.title", "dc.contributor.author", "dc.creator", "dc.date.issued",
            "dc.publisher", "dc.relation.ispartof"
        ],
        "publication_type": [
            "dc.type"
        ],
        "publication_indexing": [
            "dc.description", "dc.subject", "dc.description.abstract"
        ],
        "person": [
            "person.familyName", "person.givenName", "person.identifier.gnd"
        ]
    }

    # Distribute metadata into sections
    for section, fields in section_mappings.items():
        section_metadata = {}
        for field in fields:
            if field in metadata:
                section_metadata[field] = metadata[field]
        if section_metadata:
            sections[section] = section_metadata

    # Add any remaining fields to a general section
    used_fields = set()
    for fields in section_mappings.values():
        used_fields.update(fields)

    remaining_fields = {k: v for k, v in metadata.items() if k not in used_fields}
    if remaining_fields:
        sections["additional"] = remaining_fields

    return sections

def merge_metadata_sections(*section_dicts):
    """
    Merge multiple metadata section dictionaries

    Parameters:
        *section_dicts: Multiple section dictionaries to merge

    Returns:
        dict: Merged sections
    """
    merged = {}

    for section_dict in section_dicts:
        for section_name, metadata in section_dict.items():
            if section_name not in merged:
                merged[section_name] = {}
            merged[section_name].update(metadata)

    return merged

def validate_uuid(uuid_string):
    """
    Validate UUID format

    Parameters:
        uuid_string: String to validate

    Returns:
        bool: True if valid UUID format
    """
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )
    return bool(uuid_pattern.match(uuid_string)) if uuid_string else False

def convert_date_format(date_string, input_format=None, output_format="%Y"):
    """
    Convert date between different formats

    Parameters:
        date_string: Date string to convert
        input_format: Input format (auto-detected if None)
        output_format: Desired output format

    Returns:
        str: Converted date string
    """
    if not date_string:
        return None

    common_formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ"
    ]

    parsed_date = None

    if input_format:
        try:
            parsed_date = datetime.strptime(date_string, input_format)
        except ValueError:
            pass

    if not parsed_date:
        for fmt in common_formats:
            try:
                parsed_date = datetime.strptime(date_string, fmt)
                break
            except ValueError:
                continue

    if parsed_date:
        return parsed_date.strftime(output_format)
    else:
        # If all parsing fails, try to extract year
        year_match = re.search(r'\b(19|20)\d{2}\b', date_string)
        return year_match.group(0) if year_match else date_string

def create_dublin_core_metadata(title=None, creators=None, contributors=None,
                               description=None, subjects=None, date_issued=None,
                               type_value=None, language="en", **kwargs):
    """
    Create Dublin Core metadata structure

    Parameters:
        title: Item title
        creators: List of creators
        contributors: List of contributors
        description: Item description
        subjects: List of subjects
        date_issued: Date of issue (year or full date)
        type_value: Resource type
        language: Default language for metadata
        **kwargs: Additional metadata fields

    Returns:
        dict: Formatted Dublin Core metadata
    """
    metadata = {}

    if title:
        metadata["dc.title"] = format_metadata_field(title, language)

    if creators:
        metadata["dc.creator"] = format_metadata_field(creators, language)

    if contributors:
        metadata["dc.contributor.author"] = format_metadata_field(contributors, language)

    if description:
        metadata["dc.description"] = format_metadata_field(description, language)

    if subjects:
        metadata["dc.subject"] = format_metadata_field(subjects, language)

    if date_issued:
        metadata["dc.date.issued"] = format_metadata_field(str(date_issued))

    if type_value:
        metadata["dc.type"] = format_metadata_field(type_value)

    # Add any additional metadata fields
    for field, value in kwargs.items():
        if value:
            metadata[field] = format_metadata_field(value, language)

    return metadata

def create_publication_metadata(title, authors=None, date_issued=None,
                              pub_type="text", pub_type_authority=None,
                              description=None, language="en"):
    """
    Create metadata for a publication item

    Parameters:
        title: Publication title
        authors: List of authors
        date_issued: Publication date
        pub_type: Publication type
        pub_type_authority: Authority URI for publication type
        description: Publication description
        language: Default language

    Returns:
        dict: Publication metadata sections
    """
    metadata_sections = {}

    # Publication section
    publication_metadata = {"dc.title": format_metadata_field(title, language)}

    if authors:
        publication_metadata["dc.contributor.author"] = format_metadata_field(authors, language)

    if date_issued:
        publication_metadata["dc.date.issued"] = format_metadata_field(str(date_issued))

    metadata_sections["publication"] = publication_metadata

    # Publication type section
    if pub_type:
        type_value = format_metadata_value(pub_type, authority=pub_type_authority, confidence=600)
        metadata_sections["publication_type"] = {"dc.type": [type_value]}

    # Description section
    if description:
        metadata_sections["publication_indexing"] = {
            "dc.description": format_metadata_field(description, language)
        }

    return metadata_sections
