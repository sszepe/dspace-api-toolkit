"""
Tests for DSpace workspace module
"""

import pytest
from unittest.mock import Mock
from dspace_api.workspaces import WorkspaceManager
from dspace_api.exceptions import BadRequestError, NotFoundError, ServerError

class TestWorkspaceManager:
    """Test cases for WorkspaceManager"""

    def test_init(self, mock_auth):
        """Test WorkspaceManager initialization"""
        manager = WorkspaceManager(mock_auth)

        assert manager.auth == mock_auth
        assert manager.session == mock_auth.session
        assert manager.config == mock_auth.config

    def test_create_workspace_item_success(self, mock_auth, mock_responses, api_helpers):
        """Test successful workspace item creation"""
        manager = WorkspaceManager(mock_auth)
        collection_uuid = "test-collection-uuid"

        api_helpers.mock_api_response(
            manager.session,
            "post",
            201,
            json_data=mock_responses.workspace_item_created
        )

        result = manager.create_workspace_item(collection_uuid, "Publication")

        assert result == mock_responses.workspace_item_created
        assert result["id"] == 123

        # Verify correct API call
        call_args = manager.session.post.call_args
        url = call_args[0][0]
        assert "workspaceitems" in url
        assert f"owningCollection={collection_uuid}" in url
        assert "entityType=Publication" in url

    def test_create_workspace_item_different_entity_type(self, mock_auth, mock_responses, api_helpers):
        """Test workspace item creation with different entity type"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "post",
            201,
            json_data=mock_responses.workspace_item_created
        )

        result = manager.create_workspace_item("collection-uuid", "Person")

        # Verify entity type parameter
        call_args = manager.session.post.call_args
        assert "entityType=Person" in call_args[0][0]

    def test_create_workspace_item_error(self, mock_auth, api_helpers):
        """Test workspace item creation with error"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(manager.session, "post", 400)

        with pytest.raises(BadRequestError):
            manager.create_workspace_item("collection-uuid", "Publication")

    def test_get_workspace_item_success(self, mock_auth, mock_responses, api_helpers):
        """Test successful workspace item retrieval"""
        manager = WorkspaceManager(mock_auth)
        workspace_id = 123

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.workspace_item_created
        )

        result = manager.get_workspace_item(workspace_id)

        assert result == mock_responses.workspace_item_created
        api_helpers.assert_api_call_made(manager.session, "GET", f"/workspaceitems/{workspace_id}")

    def test_get_workspace_item_not_found(self, mock_auth, api_helpers):
        """Test get workspace item when not found"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(manager.session, "get", 404)

        with pytest.raises(NotFoundError):
            manager.get_workspace_item(999)

    def test_patch_workspace_item_success(self, mock_auth, mock_responses, api_helpers):
        """Test successful workspace item patching"""
        manager = WorkspaceManager(mock_auth)
        workspace_id = 123

        operations = [
            {
                "op": "add",
                "path": "/sections/publication/dc.title",
                "value": [{"value": "Test Title", "language": "en"}]
            }
        ]

        api_helpers.mock_api_response(
            manager.session,
            "patch",
            200,
            json_data=mock_responses.workspace_item_with_metadata
        )

        result = manager.patch_workspace_item(workspace_id, operations)

        assert result == mock_responses.workspace_item_with_metadata

        # Verify PATCH call with operations
        call_args = manager.session.patch.call_args
        assert call_args[1]["json"] == operations

    def test_patch_workspace_item_error(self, mock_auth, api_helpers):
        """Test workspace item patching with error"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(manager.session, "patch", 400)

        operations = [{"op": "invalid", "path": "/invalid"}]

        with pytest.raises(BadRequestError):
            manager.patch_workspace_item(123, operations)

    def test_add_metadata_string_value(self, mock_auth, mock_responses, api_helpers):
        """Test adding metadata with string value"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "patch",
            200,
            json_data=mock_responses.workspace_item_with_metadata
        )

        result = manager.add_metadata(123, "publication", "dc.title", "Test Title")

        # Verify operation was created correctly
        call_args = manager.session.patch.call_args
        operations = call_args[1]["json"]

        assert len(operations) == 1
        assert operations[0]["op"] == "add"
        assert operations[0]["path"] == "/sections/publication/dc.title"
        assert operations[0]["value"] == [{"value": "Test Title"}]

    def test_add_metadata_list_values(self, mock_auth, mock_responses, api_helpers):
        """Test adding metadata with list values"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "patch",
            200,
            json_data=mock_responses.workspace_item_with_metadata
        )

        authors = ["Author One", "Author Two"]
        result = manager.add_metadata(123, "publication", "dc.contributor.author", authors)

        # Verify list was converted to proper format
        call_args = manager.session.patch.call_args
        operations = call_args[1]["json"]

        expected_value = [{"value": "Author One"}, {"value": "Author Two"}]
        assert operations[0]["value"] == expected_value

    def test_add_metadata_complex_values(self, mock_auth, mock_responses, api_helpers):
        """Test adding metadata with complex values"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "patch",
            200,
            json_data=mock_responses.workspace_item_with_metadata
        )

        complex_values = [{
            "value": "still image",
            "authority": "publication-coar-types:c_ecc8",
            "confidence": 600
        }]

        result = manager.add_metadata(123, "publication_type", "dc.type", complex_values)

        # Verify complex values are preserved
        call_args = manager.session.patch.call_args
        operations = call_args[1]["json"]
        assert operations[0]["value"] == complex_values

    def test_set_metadata_multiple_sections(self, mock_auth, mock_responses, api_helpers):
        """Test setting multiple metadata sections at once"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "patch",
            200,
            json_data=mock_responses.workspace_item_with_metadata
        )

        metadata_sections = {
            "publication": {
                "dc.title": [{"value": "Test Publication", "language": "en"}],
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

        result = manager.set_metadata(123, metadata_sections)

        # Verify all operations were created
        call_args = manager.session.patch.call_args
        operations = call_args[1]["json"]

        assert len(operations) == 3  # 2 from publication + 1 from publication_type

        # Check operation paths
        paths = [op["path"] for op in operations]
        assert "/sections/publication/dc.title" in paths
        assert "/sections/publication/dc.date.issued" in paths
        assert "/sections/publication_type/dc.type" in paths

    def test_delete_workspace_item_success(self, mock_auth, api_helpers):
        """Test successful workspace item deletion"""
        manager = WorkspaceManager(mock_auth)
        workspace_id = 123

        api_helpers.mock_api_response(manager.session, "delete", 204)

        result = manager.delete_workspace_item(workspace_id)

        assert result is True
        api_helpers.assert_api_call_made(manager.session, "DELETE", f"/workspaceitems/{workspace_id}")

    def test_delete_workspace_item_not_found(self, mock_auth, api_helpers):
        """Test delete workspace item when not found"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(manager.session, "delete", 404)

        with pytest.raises(NotFoundError):
            manager.delete_workspace_item(999)

    def test_deposit_to_workflow_success(self, mock_auth, mock_responses, api_helpers):
        """Test successful deposit to workflow"""
        manager = WorkspaceManager(mock_auth)
        workspace_id = 123

        api_helpers.mock_api_response(
            manager.session,
            "post",
            201,
            text_data=mock_responses.workflow_item_response
        )

        result = manager.deposit_to_workflow(workspace_id)

        assert result == mock_responses.workflow_item_response

        # Verify POST call with correct headers and data
        call_args = manager.session.post.call_args
        assert "workflowitems" in call_args[0][0]
        assert call_args[1]["headers"]["Content-Type"] == "text/uri-list"
        assert f"workspaceitems/{workspace_id}" in call_args[1]["data"]

    def test_deposit_to_workflow_error(self, mock_auth, api_helpers):
        """Test deposit to workflow with error"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(manager.session, "post", 400)

        with pytest.raises(BadRequestError):
            manager.deposit_to_workflow(123)

    def test_get_workspace_items_success(self, mock_auth, api_helpers):
        """Test successful retrieval of workspace items list"""
        manager = WorkspaceManager(mock_auth)

        workspace_items_list = {
            "_embedded": {
                "workspaceitems": [
                    {"id": 1, "uuid": "ws-1"},
                    {"id": 2, "uuid": "ws-2"}
                ]
            },
            "page": {"totalElements": 2}
        }

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=workspace_items_list
        )

        result = manager.get_workspace_items(page=1, size=5)

        assert result == workspace_items_list

        # Verify parameters
        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert params["page"] == 1
        assert params["size"] == 5

    def test_create_publication_item_success(self, mock_auth, mock_responses, api_helpers):
        """Test successful creation of publication workspace item"""
        manager = WorkspaceManager(mock_auth)

        # Mock workspace creation
        manager.create_workspace_item = Mock(return_value={"id": 123})
        manager.set_metadata = Mock(return_value=mock_responses.workspace_item_with_metadata)

        result = manager.create_publication_item(
            collection_uuid="collection-uuid",
            title="Test Publication",
            description="Test description",
            date_issued="2025",
            pub_type="journal article",
            pub_type_authority="publication-coar-types:c_6501"
        )

        assert result == mock_responses.workspace_item_with_metadata

        # Verify workspace item was created
        manager.create_workspace_item.assert_called_once_with("collection-uuid", "Publication")

        # Verify metadata was set correctly
        manager.set_metadata.assert_called_once()
        call_args = manager.set_metadata.call_args
        metadata_sections = call_args[0][1]

        # Check publication section
        pub_section = metadata_sections["publication"]
        assert pub_section["dc.title"][0]["value"] == "Test Publication"
        assert pub_section["dc.date.issued"][0]["value"] == "2025"

        # Check publication type section
        type_section = metadata_sections["publication_type"]
        assert type_section["dc.type"][0]["value"] == "journal article"
        assert type_section["dc.type"][0]["authority"] == "publication-coar-types:c_6501"

        # Check description section
        desc_section = metadata_sections["publication_indexing"]
        assert desc_section["dc.description"][0]["value"] == "Test description"

    def test_create_publication_item_minimal_data(self, mock_auth, mock_responses):
        """Test create publication item with minimal data"""
        manager = WorkspaceManager(mock_auth)

        manager.create_workspace_item = Mock(return_value={"id": 456})
        manager.set_metadata = Mock(return_value=mock_responses.workspace_item_with_metadata)

        result = manager.create_publication_item(
            collection_uuid="collection-uuid",
            title="Minimal Publication"
        )

        # Verify only required sections are created
        call_args = manager.set_metadata.call_args
        metadata_sections = call_args[0][1]

        assert "publication" in metadata_sections
        assert "publication_type" in metadata_sections
        assert "publication_indexing" not in metadata_sections  # No description provided

    def test_create_publication_item_no_workspace_id(self, mock_auth):
        """Test create publication item when workspace creation fails"""
        manager = WorkspaceManager(mock_auth)

        manager.create_workspace_item = Mock(return_value={})  # No ID

        with pytest.raises(BadRequestError, match="Failed to get workspace item ID"):
            manager.create_publication_item("collection-uuid", "Test Title")

    def test_exception_handling_in_methods(self, mock_auth):
        """Test exception handling in various methods"""
        manager = WorkspaceManager(mock_auth)

        # Mock session to raise exception
        import requests
        manager.session.get = Mock(side_effect=requests.exceptions.ConnectionError("Network error"))

        with pytest.raises(requests.exceptions.ConnectionError):
            manager.get_workspace_items()

        with pytest.raises(requests.exceptions.ConnectionError):
            manager.get_workspace_item(123)

    def test_deposit_workflow_with_request_exception(self, mock_auth):
        """Test deposit to workflow with request exception"""
        manager = WorkspaceManager(mock_auth)

        import requests
        manager.session.post = Mock(side_effect=requests.exceptions.Timeout("Timeout"))

        with pytest.raises(requests.exceptions.Timeout):
            manager.deposit_to_workflow(123)

    def test_patch_operations_structure(self, mock_auth, mock_responses, api_helpers):
        """Test that patch operations are structured correctly"""
        manager = WorkspaceManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "patch",
            200,
            json_data=mock_responses.workspace_item_with_metadata
        )

        # Test with complex metadata structure
        metadata_sections = {
            "publication": {
                "dc.contributor.author": [
                    {"value": "Last, First", "language": None, "authority": "auth123", "confidence": 600},
                    {"value": "Another, Author", "language": None, "authority": None, "confidence": -1}
                ]
            }
        }

        result = manager.set_metadata(123, metadata_sections)

        # Verify the patch operation preserves the complex structure
        call_args = manager.session.patch.call_args
        operations = call_args[1]["json"]

        assert len(operations) == 1
        assert operations[0]["op"] == "add"
        assert operations[0]["path"] == "/sections/publication/dc.contributor.author"

        # Verify complex author data is preserved
        author_values = operations[0]["value"]
        assert len(author_values) == 2
        assert author_values[0]["authority"] == "auth123"
        assert author_values[1]["authority"] is None

    def test_create_publication_item_date_only(self, mock_auth, mock_responses):
        """Test creating a publication item with only a date issued"""
        mgr = WorkspaceManager(mock_auth)
        mgr.create_workspace_item = Mock(return_value={"id": 777})
        mgr.set_metadata = Mock(return_value=mock_responses.workspace_item_with_metadata)

        _ = mgr.create_publication_item(
            collection_uuid="col-uuid",
            title="Only Date",
            date_issued="2024",
            description=None,
            pub_type="still image",
            pub_type_authority="publication-coar-types:c_ecc8",
        )

        args, _ = mgr.set_metadata.call_args
        sections = args[1]
        assert "publication" in sections
        assert "dc.date.issued" in sections["publication"]
        assert "publication_indexing" not in sections

    def test_list_workspace_items_error_branch(self, mock_auth):
        """Test error handling in list_workspace_items"""
        mgr = WorkspaceManager(mock_auth)
        # Force non-200 so line 275 (handle_response_error) is executed
        mgr.session.get = Mock(return_value=Mock(status_code=500, text="boom"))
        with pytest.raises(ServerError):
            mgr.get_workspace_items(page=0, size=5)


    def test_get_workspace_item_422_returns_error_dict(self, mock_auth, api_helpers):
        """Test get_workspace_item maps 422 to an error dict instead of raising"""
        manager = WorkspaceManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "get", 422)
        assert manager.get_workspace_item(42) == {"error": "Workspace item cannot be deposited automatically."}

    def test_patch_workspace_item_422_returns_error_dict(self, mock_auth, api_helpers):
        """Test patch_workspace_item maps 422 to an error dict instead of raising"""
        manager = WorkspaceManager(mock_auth)
        api_helpers.mock_api_response(manager.session, "patch", 422)
        out = manager.patch_workspace_item(42, [{"op": "add", "path": "/sections/x", "value": []}])
        assert out == {"error": "Workspace item cannot be deposited automatically."}
