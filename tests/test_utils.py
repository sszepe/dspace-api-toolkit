"""
Tests for DSpace utility functions
"""

import pytest
import os
import tempfile
import logging
from datetime import datetime
from unittest.mock import patch, Mock
from dspace_api.utils import (
    format_metadata_value,
    format_metadata_field,
    create_dublin_core_metadata,
    create_publication_metadata,
    extract_uuid_from_url,
    parse_handle,
    validate_metadata_field,
    sanitize_filename,
    create_patch_operation,
    batch_metadata_operations,
    format_file_size,
    check_file_exists_and_readable,
    get_file_info,
    create_workspace_sections_from_metadata,
    merge_metadata_sections,
    validate_uuid,
    convert_date_format,
    log_api_call
)

# Fixtures
@pytest.fixture
def temp_test_file():
    """Create a temporary test file"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("Test content")
        temp_path = f.name

    yield temp_path

    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


class TestMetadataFormatting:
    """Test metadata formatting utilities"""

    def test_format_metadata_value_basic(self):
        """Test basic metadata value formatting"""
        result = format_metadata_value("Test Value")

        expected = {
            "value": "Test Value",
            "confidence": -1,
            "place": 0
        }
        assert result == expected

    def test_format_metadata_value_with_all_params(self):
        """Test metadata value formatting with all parameters"""
        result = format_metadata_value(
            "Test Value",
            language="en",
            authority="auth123",
            confidence=600,
            place=2
        )

        expected = {
            "value": "Test Value",
            "language": "en",
            "authority": "auth123",
            "confidence": 600,
            "place": 2
        }
        assert result == expected

    def test_format_metadata_value_removes_none(self):
        """Test that None values are removed from formatted metadata"""
        result = format_metadata_value("Test", language=None, authority=None)

        assert "language" not in result
        assert "authority" not in result
        assert result["value"] == "Test"

    def test_format_metadata_field_single_string(self):
        """Test formatting metadata field with single string"""
        result = format_metadata_field("Single Value", default_language="de")

        expected = [{
            "value": "Single Value",
            "language": "de",
            "confidence": -1,
            "place": 0
        }]
        assert result == expected

    def test_format_metadata_field_list_of_strings(self):
        """Test formatting metadata field with list of strings"""
        values = ["Value 1", "Value 2", "Value 3"]
        result = format_metadata_field(values, default_language="en")

        assert len(result) == 3
        for i, item in enumerate(result):
            assert item["value"] == f"Value {i+1}"
            assert item["language"] == "en"
            assert item["place"] == i

    def test_format_metadata_field_list_of_dicts(self):
        """Test formatting metadata field with list of dictionaries"""
        values = [
            {"value": "Dict Value 1", "authority": "auth1"},
            {"value": "Dict Value 2", "language": "fr"}
        ]
        result = format_metadata_field(values)

        assert len(result) == 2
        assert result[0]["value"] == "Dict Value 1"
        assert result[0]["authority"] == "auth1"
        assert result[0]["place"] == 0
        assert result[1]["place"] == 1

    def test_format_metadata_field_non_list_non_string(self):
        """Test formatting metadata field with non-string, non-list input"""
        result = format_metadata_field(1234, default_language="en")
        expected = [{"value": "1234", "language": "en", "confidence": -1, "place": 0}]
        assert result == expected

    def test_format_metadata_field_mixed_types(self):
        """Test formatting metadata field with mixed types"""
        values = [
            "String value",
            {"value": "Dict value", "language": "es"},
            123,
            None
        ]
        result = format_metadata_field(values)

        assert len(result) == 4
        assert result[0]["value"] == "String value"
        assert result[1]["value"] == "Dict value"
        assert result[1]["language"] == "es"
        assert result[2]["value"] == "123"
        assert result[3]["value"] == "None"


class TestDublinCoreMetadata:
    """Test Dublin Core metadata creation"""

    def test_create_dublin_core_metadata_basic(self):
        """Test basic Dublin Core metadata creation"""
        result = create_dublin_core_metadata(
            title="Test Publication",
            creators=["Creator One", "Creator Two"],
            description="Test description"
        )

        assert "dc.title" in result
        assert "dc.creator" in result
        assert "dc.description" in result

        assert result["dc.title"][0]["value"] == "Test Publication"
        assert len(result["dc.creator"]) == 2
        assert result["dc.creator"][0]["value"] == "Creator One"

    def test_create_dublin_core_metadata_with_language(self):
        """Test Dublin Core metadata creation with specific language"""
        result = create_dublin_core_metadata(
            title="Deutscher Titel",
            description="Deutsche Beschreibung",
            language="de"
        )

        assert result["dc.title"][0]["language"] == "de"
        assert result["dc.description"][0]["language"] == "de"

    def test_create_dublin_core_metadata_with_additional_fields(self):
        """Test Dublin Core metadata creation with additional fields"""
        result = create_dublin_core_metadata(
            title="Test Title",
            **{
                "dc.identifier.uri": "http://example.com",
                "dc.rights": "CC BY 4.0"
            }
        )

        assert "dc.title" in result
        assert "dc.identifier.uri" in result
        assert "dc.rights" in result

    def test_create_dublin_core_metadata_all_fields(self):
        """Test Dublin Core metadata creation with all fields"""
        md = create_dublin_core_metadata(
            title="T",
            creators=["A"],
            contributors=["B"],
            description="D",
            subjects=["S1", "S2"],
            date_issued="2023-01-01",
            type_value="Article",
            language="en",
        )
        assert md["dc.title"][0]["value"] == "T"
        assert md["dc.creator"][0]["value"] == "A"
        assert md["dc.contributor.author"][0]["value"] == "B"
        assert [v["value"] for v in md["dc.subject"]] == ["S1", "S2"]
        assert md["dc.date.issued"][0]["value"] == "2023-01-01"
        assert md["dc.type"][0]["value"] == "Article"


class TestPublicationMetadata:
    """Test publication metadata creation"""

    def test_create_publication_metadata_basic(self):
        """Test basic publication metadata creation"""
        result = create_publication_metadata(
            title="Test Publication",
            authors=["Author One", "Author Two"],
            date_issued="2025",
            pub_type=None  # Explicitly set to None to avoid default
        )

        assert "publication" in result

        pub_section = result["publication"]
        assert pub_section["dc.title"][0]["value"] == "Test Publication"
        assert len(pub_section["dc.contributor.author"]) == 2

    def test_create_publication_metadata_with_type(self):
        """Test publication metadata creation with type"""
        result = create_publication_metadata(
            title="Test Article",
            pub_type="journal article",
            pub_type_authority="publication-coar-types:c_6501"
        )

        assert "publication_type" in result
        type_section = result["publication_type"]
        assert type_section["dc.type"][0]["value"] == "journal article"
        assert type_section["dc.type"][0]["authority"] == "publication-coar-types:c_6501"

    def test_create_publication_metadata_with_description(self):
        """Test publication metadata creation with description"""
        result = create_publication_metadata(
            title="Test Publication",
            description="Test description"
        )

        assert "publication_indexing" in result
        desc_section = result["publication_indexing"]
        assert desc_section["dc.description"][0]["value"] == "Test description"


class TestUtilityFunctions:
    """Test various utility functions"""

    def test_extract_uuid_from_url(self):
        """Test UUID extraction from URL"""
        url = "http://localhost:8080/server/api/core/items/12345678-1234-1234-1234-123456789012"
        result = extract_uuid_from_url(url)

        assert result == "12345678-1234-1234-1234-123456789012"

    def test_extract_uuid_from_url_none(self):
        """Test UUID extraction with None input"""
        result = extract_uuid_from_url(None)
        assert result is None

    def test_extract_uuid_from_url_no_uuid(self):
        """Test UUID extraction when no UUID in URL"""
        url = "http://localhost:8080/server/api/core/items"
        result = extract_uuid_from_url(url)
        assert result is None

    def test_parse_handle_basic(self):
        """Test basic handle parsing"""
        handle = "123456789/123"
        result = parse_handle(handle)
        assert result == "123456789/123"

    def test_parse_handle_from_url(self):
        """Test handle parsing from URL"""
        url = "http://hdl.handle.net/123456789/456"
        result = parse_handle(url)
        assert result == "123456789/456"

    def test_parse_handle_https_url(self):
        """Test handle parsing from HTTPS URL"""
        url = "https://hdl.handle.net/123456789/789"
        result = parse_handle(url)
        assert result == "123456789/789"

    def test_parse_handle_none(self):
        """Test handle parsing with None input"""
        result = parse_handle(None)
        assert result is None

    def test_validate_metadata_field_empty_name(self):
        """Test metadata field validation with empty field name"""
        with pytest.raises(ValueError, match="Field name cannot be empty"):
            validate_metadata_field("", ["Test"])

    def test_validate_metadata_field_no_values(self):
        """Test metadata field validation with no values"""
        with pytest.raises(ValueError, match="No values provided"):
            validate_metadata_field("dc.title", [])

    def test_validate_metadata_field_invalid_dc_format(self):
        """Test metadata field validation with invalid Dublin Core format"""
        with pytest.raises(ValueError, match="Invalid Dublin Core field format"):
            validate_metadata_field("dc.invalid", ["Test"])

    def test_validate_metadata_field_valid(self):
        """Test metadata field validation with valid field"""
        result = validate_metadata_field("dc.title.alternative", ["Test"])
        assert result is True

    def test_sanitize_filename_basic(self):
        """Test basic filename sanitization"""
        filename = "test_file.pdf"
        result = sanitize_filename(filename)
        assert result == "test_file.pdf"

    def test_sanitize_filename_with_invalid_chars(self):
        """Test filename sanitization with invalid characters"""
        filename = 'test<>:"/\\|?*file.pdf'
        result = sanitize_filename(filename)
        assert "test" in result and "file.pdf" in result
        assert len([c for c in '<>:"/\\|?*' if c in result]) == 0

    def test_sanitize_filename_long_name(self):
        """Test filename sanitization with very long name"""
        long_filename = "a" * 300 + ".txt"
        result = sanitize_filename(long_filename)
        assert len(result) <= 255
        assert result.endswith(".txt")

    def test_sanitize_filename_with_whitespace_and_dots(self):
        """Test filename sanitization with leading/trailing whitespace and dots"""
        filename = "  .test_file.txt.  "
        result = sanitize_filename(filename)
        assert not result.startswith(" ") and not result.startswith(".")
        assert not result.endswith(" ") and not result.endswith(".")

    def test_create_patch_operation_add(self):
        """Test creating add patch operation"""
        result = create_patch_operation("add", "/metadata/dc.title", [{"value": "Test"}])

        expected = {
            "op": "add",
            "path": "/metadata/dc.title",
            "value": [{"value": "Test"}]
        }
        assert result == expected

    def test_create_patch_operation_move(self):
        """Test creating move patch operation"""
        result = create_patch_operation("move", "/new/path", from_path="/old/path")

        expected = {
            "op": "move",
            "path": "/new/path",
            "from": "/old/path"
        }
        assert result == expected

    def test_create_patch_operation_various_ops(self):
        """Test creating various patch operations"""
        # 'test' op uses value
        t = create_patch_operation("test", "/p", value={"x": 1})
        assert t == {"op": "test", "path": "/p", "value": {"x": 1}}

        # 'copy' op uses from_path
        c = create_patch_operation("copy", "/to", from_path="/from")
        assert c == {"op": "copy", "path": "/to", "from": "/from"}

        # 'add' with value=None should NOT include 'value'
        a = create_patch_operation("add", "/p", value=None)
        assert a == {"op": "add", "path": "/p"}

    def test_create_patch_operation_remove(self):
        """Test creating remove patch operation"""
        result = create_patch_operation("remove", "/metadata/dc.subject")
        expected = {
            "op": "remove",
            "path": "/metadata/dc.subject"
        }
        assert result == expected

    def test_batch_metadata_operations(self):
        """Test batch metadata operations creation"""
        metadata_updates = {
            "dc.title": "New Title",
            "dc.description": ["Desc 1", "Desc 2"]
        }

        result = batch_metadata_operations(metadata_updates)

        assert len(result) == 2
        assert result[0]["op"] == "replace"
        assert result[0]["path"] == "/metadata/dc.title"
        assert result[1]["path"] == "/metadata/dc.description"


class TestLoggingUtilities:
    """Test logging utility functions"""

    def test_log_api_call_levels_and_timing(self, caplog):
        """Test API call logging with different levels and timing"""
        # INFO (2xx) with response_time
        with caplog.at_level(logging.INFO):
            log_api_call("GET", "/ok", 200, response_time=0.12)
        assert any("GET /ok -> 200" in r.message and "(0.12" in r.message for r in caplog.records)

        # WARNING (4xx)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            log_api_call("GET", "/warn", 404)
        assert any(r.levelno == logging.WARNING and "GET /warn -> 404" in r.message for r in caplog.records)

        # ERROR (everything else, e.g., 5xx)
        caplog.clear()
        with caplog.at_level(logging.ERROR):
            log_api_call("GET", "/err", 500)
        assert any(r.levelno == logging.ERROR and "GET /err -> 500" in r.message for r in caplog.records)

    def test_log_api_call_without_timing(self, caplog):
        """Test API call logging without timing information"""
        with caplog.at_level(logging.INFO):
            log_api_call("POST", "/test", 201)
        assert any("POST /test -> 201" in r.message for r in caplog.records)


class TestFileUtilities:
    """Test file-related utility functions"""

    def test_format_file_size_bytes(self):
        """Test file size formatting in bytes"""
        assert format_file_size(0) == "0 B"
        assert format_file_size(500) == "500.0 B"

    def test_format_file_size_kb(self):
        """Test file size formatting in KB"""
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(1536) == "1.5 KB"

    def test_format_file_size_mb(self):
        """Test file size formatting in MB"""
        assert format_file_size(1024 * 1024) == "1.0 MB"
        assert format_file_size(1024 * 1024 * 2.5) == "2.5 MB"

    def test_format_file_size_gb(self):
        """Test file size formatting in GB"""
        assert format_file_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_format_file_size_tb(self):
        """Test file size formatting in TB"""
        assert format_file_size(1024 * 1024 * 1024 * 1024) == "1.0 TB"

    def test_check_file_exists_and_readable_success(self, temp_test_file):
        """Test file existence and readability check with valid file"""
        result = check_file_exists_and_readable(temp_test_file)
        assert result is True

    def test_check_file_exists_and_readable_not_exists(self):
        """Test file existence check with non-existent file"""
        with pytest.raises(FileNotFoundError):
            check_file_exists_and_readable("/nonexistent/file.txt")

    @patch('os.access')
    @patch('os.path.exists')
    def test_check_file_exists_and_readable_not_readable(self, mock_exists, mock_access):
        """Test file readability check with non-readable file"""
        mock_exists.return_value = True
        mock_access.return_value = False

        with pytest.raises(PermissionError):
            check_file_exists_and_readable("/unreadable/file.txt")

    def test_get_file_info(self, temp_test_file):
        """Test getting file information"""
        result = get_file_info(temp_test_file)

        assert "path" in result
        assert "name" in result
        assert "size" in result
        assert "size_formatted" in result
        assert "modified" in result
        assert "extension" in result

        assert result["path"] == temp_test_file
        assert result["name"] == os.path.basename(temp_test_file)
        assert isinstance(result["modified"], datetime)


class TestWorkspaceUtilities:
    """Test workspace-related utility functions"""

    def test_create_workspace_sections_from_metadata(self):
        """Test creating workspace sections from flat metadata"""
        flat_metadata = {
            "dc.title": [{"value": "Test Title"}],
            "dc.contributor.author": [{"value": "Author"}],
            "dc.description": [{"value": "Description"}],
            "dc.type": [{"value": "article"}],
            "person.familyName": [{"value": "Smith"}],
            "custom.field": [{"value": "Custom"}]
        }

        result = create_workspace_sections_from_metadata(flat_metadata)

        assert "publication" in result
        assert "publication_type" in result
        assert "publication_indexing" in result
        assert "person" in result
        assert "additional" in result

        assert result["publication"]["dc.title"] == flat_metadata["dc.title"]
        assert result["publication_type"]["dc.type"] == flat_metadata["dc.type"]
        assert result["additional"]["custom.field"] == flat_metadata["custom.field"]

    def test_create_workspace_sections_empty_metadata(self):
        """Test creating workspace sections with empty metadata"""
        result = create_workspace_sections_from_metadata({})
        assert result == {}

    def test_create_workspace_sections_partial_metadata(self):
        """Test creating workspace sections with partial metadata"""
        flat_metadata = {
            "dc.title": [{"value": "Title Only"}]
        }

        result = create_workspace_sections_from_metadata(flat_metadata)
        assert "publication" in result
        assert "publication_type" not in result
        assert result["publication"]["dc.title"] == flat_metadata["dc.title"]

    def test_merge_metadata_sections(self):
        """Test merging multiple metadata section dictionaries"""
        section1 = {
            "publication": {"dc.title": [{"value": "Title 1"}]},
            "publication_type": {"dc.type": [{"value": "article"}]}
        }

        section2 = {
            "publication": {"dc.description": [{"value": "Description"}]},
            "indexing": {"dc.subject": [{"value": "Subject"}]}
        }

        result = merge_metadata_sections(section1, section2)

        # Check merged publication section
        assert "dc.title" in result["publication"]
        assert "dc.description" in result["publication"]

        # Check other sections preserved
        assert "publication_type" in result
        assert "indexing" in result

    def test_merge_metadata_sections_empty(self):
        """Test merging empty metadata sections"""
        result = merge_metadata_sections({}, {})
        assert result == {}

    def test_merge_metadata_sections_single(self):
        """Test merging single metadata section"""
        section = {"publication": {"dc.title": [{"value": "Title"}]}}
        result = merge_metadata_sections(section)
        assert result == section


class TestValidationUtilities:
    """Test validation utility functions"""

    def test_validate_uuid_valid(self):
        """Test UUID validation with valid UUID"""
        valid_uuid = "12345678-1234-1234-1234-123456789012"
        assert validate_uuid(valid_uuid) is True

    def test_validate_uuid_valid_uppercase(self):
        """Test UUID validation with valid uppercase UUID"""
        valid_uuid = "12345678-1234-1234-1234-123456789ABC"
        assert validate_uuid(valid_uuid) is True

    def test_validate_uuid_invalid_format(self):
        """Test UUID validation with invalid format"""
        invalid_uuid = "12345678-1234-1234-123456789012"  # Missing section
        assert validate_uuid(invalid_uuid) is False

    def test_validate_uuid_invalid_characters(self):
        """Test UUID validation with invalid characters"""
        invalid_uuid = "12345678-1234-1234-1234-12345678901g"  # 'g' is invalid
        assert validate_uuid(invalid_uuid) is False

    def test_validate_uuid_none(self):
        """Test UUID validation with None input"""
        assert validate_uuid(None) is False

    def test_validate_uuid_empty_string(self):
        """Test UUID validation with empty string"""
        assert validate_uuid("") is False

    def test_validate_metadata_field_valid_non_dc(self):
        """Test metadata field validation with valid non-Dublin Core field"""
        result = validate_metadata_field("person.familyName", ["Test"])
        assert result is True

    def test_validate_metadata_field_valid_dc(self):
        """Test metadata field validation with valid Dublin Core field"""
        result = validate_metadata_field("dc.title.alternative", ["Test"])
        assert result is True


class TestDateUtilities:
    """Test date conversion utilities"""

    def test_convert_date_format_year_only(self):
        """Test date conversion to year only"""
        result = convert_date_format("2025-03-15", output_format="%Y")
        assert result == "2025"

    def test_convert_date_format_full_date(self):
        """Test date conversion to full date"""
        result = convert_date_format("15.03.2025", output_format="%Y-%m-%d")
        assert result == "2025-03-15"

    def test_convert_date_format_auto_detect(self):
        """Test date conversion with auto-detection"""
        test_dates = [
            ("2025-03-15", "2025"),
            ("2025/03/15", "2025"),
            ("15.03.2025", "2025"),
            ("2025-03-15T12:30:00Z", "2025")
        ]

        for input_date, expected in test_dates:
            result = convert_date_format(input_date, output_format="%Y")
            assert result == expected

    def test_convert_date_format_with_input_format(self):
        """Test date conversion with specified input format"""
        result = convert_date_format("2025/12/25", input_format="%Y/%m/%d", output_format="%d.%m.%Y")
        assert result == "25.12.2025"

    def test_convert_date_format_invalid_input_format(self):
        """Test date conversion with invalid input format"""
        result = convert_date_format("2025-03-15", input_format="%Y/%m/%d", output_format="%Y")
        assert result == "2025"  # Should fall back to auto-detection

    def test_convert_date_format_invalid_date(self):
        """Test date conversion with invalid date"""
        result = convert_date_format("invalid date string")
        # Should extract year if possible
        assert result == "invalid date string"

    def test_convert_date_format_with_year_extraction(self):
        """Test date conversion with year extraction fallback"""
        result = convert_date_format("Published in 2024")
        assert result == "2024"

    def test_convert_date_format_no_year_found(self):
        """Test date conversion when no year can be extracted"""
        result = convert_date_format("no year here")
        assert result == "no year here"

    def test_convert_date_format_none_input(self):
        """Test date conversion with None input"""
        result = convert_date_format(None)
        assert result is None

    def test_convert_date_format_slash_day_first_and_iso_without_z(self):
        """Test date conversion with day-first format and ISO without Z"""
        # dd/mm/YYYY
        assert convert_date_format("15/03/2025", output_format="%Y-%m-%d") == "2025-03-15"
        # ISO without trailing Z
        assert convert_date_format("2025-03-15T12:30:00", output_format="%Y") == "2025"

    def test_convert_date_format_empty_string(self):
        """Test date conversion with empty string"""
        result = convert_date_format("")
        assert result is None


class TestCoverageEdgeCases:
    """Branches not exercised by the tests above"""

    def test_format_metadata_field_keeps_explicit_place(self):
        out = format_metadata_field([{"value": "a", "place": 5}, {"value": "b"}])
        assert out == [{"value": "a", "place": 5}, {"value": "b", "place": 1}]

    def test_create_dublin_core_metadata_without_title(self):
        md = create_dublin_core_metadata(creators=["Doe, J."])
        assert "dc.title" not in md and "dc.creator" in md

    def test_create_dublin_core_metadata_skips_empty_extra_fields(self):
        md = create_dublin_core_metadata(title="T", **{"dc.rights": "", "dc.publisher": None, "dc.source": "S"})
        assert "dc.rights" not in md and "dc.publisher" not in md
        assert md["dc.source"][0]["value"] == "S"
