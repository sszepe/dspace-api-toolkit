"""
Shared pytest fixtures and configuration for DSpace API tests
"""

import os

import pytest
import responses
from unittest.mock import Mock, MagicMock
from dspace_api import DSpaceClient
from dspace_api.auth import DSpaceAuth
from dspace_api.config import DSpaceConfig
from tests.fixtures.api_responses import MockAPIResponses

@pytest.fixture
def dspace_config():
    """Create a test DSpace configuration"""
    return DSpaceConfig(
        dspace_url="http://localhost:8080/server/api",
        version=8,
        # explicit, so DSPACE_ALL_LANGUAGES in a developer's .env can't change test results
        all_languages=False,
        # no retry sleeps in unit tests; the retry tests set their own values
        max_retries=0,
    )

# ---------------------------------------------------------------------------
# Request recording for tools/contract_coverage.py (opt-in):
#   DSPACE_RECORD_CALLS=calls.txt pytest
# appends "METHOD URL" for every request the toolkit sends - via requests
# (whatever mock answers it) and via the Mock sessions of the older tests.
# ---------------------------------------------------------------------------

_RECORD_FILE = os.getenv("DSPACE_RECORD_CALLS")


_TOOLKIT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dspace_api")


def _from_toolkit():
    """True if a toolkit module (other than the session layer) is on the call stack."""
    import sys
    frame = sys._getframe(1)
    while frame is not None:
        name = os.path.abspath(frame.f_code.co_filename)
        if name.startswith(_TOOLKIT_DIR) and not name.endswith("session.py"):
            return True
        frame = frame.f_back
    return False


def _record(method, url, check_stack=True):
    if check_stack and not _from_toolkit():
        return       # tests calling the session directly (retry tests etc.)
    if _RECORD_FILE and isinstance(url, str):
        with open(_RECORD_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"{str(method).upper()} {url}\n")


@pytest.fixture(autouse=True, scope="session")
def _record_requests():
    if not _RECORD_FILE:
        yield
        return
    import requests
    original = requests.Session.send

    def send(self, request, **kwargs):
        _record(request.method, request.url)
        return original(self, request, **kwargs)

    requests.Session.send = send
    try:
        yield
    finally:
        requests.Session.send = original


def _record_mock_calls(session):
    for name, args, kwargs in session.mock_calls:
        method = name.split(".")[-1]
        if method in ("get", "post", "put", "patch", "delete") and args:
            _record(method, args[0], check_stack=False)
        elif method == "request" and len(args) >= 2:
            _record(args[0], args[1], check_stack=False)


@pytest.fixture
def mock_session():
    """Create a mock requests session"""
    session = Mock()
    session.headers = {}
    session.cookies = {}
    session.get.return_value.status_code = 200
    session.post.return_value.status_code = 200
    session.patch.return_value.status_code = 200
    session.delete.return_value.status_code = 204
    session.put.return_value.status_code = 204
    yield session
    _record_mock_calls(session)

@pytest.fixture
def mock_auth(dspace_config, mock_session):
    """Create a mock authenticated DSpace auth instance"""
    auth = DSpaceAuth(dspace_config)
    auth.session = mock_session
    auth._authenticated = True
    return auth

@pytest.fixture
def dspace_client(mock_auth):
    """Create a test DSpace client with mocked authentication"""
    client = DSpaceClient.__new__(DSpaceClient)  # Create without calling __init__
    client.config = mock_auth.config
    client.auth = mock_auth

    # Initialize managers manually to avoid authentication
    from dspace_api.items import ItemManager
    from dspace_api.workspaces import WorkspaceManager
    from dspace_api.bundles import BundleManager
    from dspace_api.collections import CollectionManager
    from dspace_api.discovery import DiscoveryManager
    from dspace_api.vocabulary import VocabularyManager

    client.items = ItemManager(mock_auth)
    client.workspace = WorkspaceManager(mock_auth)
    client.bundles = BundleManager(mock_auth)
    client.collections = CollectionManager(mock_auth)
    client.discovery = DiscoveryManager(mock_auth)
    client.vocabulary = VocabularyManager(mock_auth)

    return client

@pytest.fixture
def mock_responses():
    """Create mock API response data"""
    return MockAPIResponses()

@pytest.fixture
def sample_item_data():
    """Sample item data for testing"""
    return {
        "uuid": "12345678-1234-1234-1234-123456789012",
        "name": "Test Item",
        "handle": "123456789/123",
        "type": "item",
        "entityType": "Publication",
        "inArchive": True,
        "discoverable": True,
        "withdrawn": False,
        "lastModified": "2025-01-01T00:00:00Z",
        "metadata": {
            "dc.title": [{"value": "Test Publication"}],
            "dc.contributor.author": [{"value": "Doe, John"}],
            "dc.date.issued": [{"value": "2025"}]
        }
    }

@pytest.fixture
def sample_collection_data():
    """Sample collection data for testing"""
    return {
        "uuid": "87654321-4321-4321-4321-210987654321",
        "name": "Test Collection",
        "handle": "123456789/456",
        "type": "collection",
        "metadata": {
            "dc.title": [{"value": "Test Collection"}],
            "dc.description": [{"value": "A test collection"}]
        }
    }

@pytest.fixture
def sample_workspace_item():
    """Sample workspace item data"""
    return {
        "id": 42,
        "uuid": "ws-123-456-789",
        "sections": {
            "publication": {
                "dc.title": [{"value": "Workspace Test Item"}]
            }
        },
        "item": {
            "uuid": "item-123-456-789",
            "entityType": "Publication"
        }
    }

@pytest.fixture
def sample_bundle_data():
    """Sample bundle data for testing"""
    return {
        "uuid": "bundle-123-456-789",
        "name": "ORIGINAL",
        "metadata": {},
        "type": "bundle"
    }

@pytest.fixture
def sample_bitstream_data():
    """Sample bitstream data for testing"""
    return {
        "uuid": "bitstream-123-456-789",
        "name": "test-file.pdf",
        "sizeBytes": 1024,
        "checkSum": {
            "checkSumAlgorithm": "MD5",
            "value": "abc123def456"
        },
        "sequenceId": 1,
        "type": "bitstream"
    }

@pytest.fixture
def sample_discovery_results():
    """Sample discovery search results"""
    return {
        "_embedded": {
            "searchResult": {
                "_embedded": {
                    "objects": [
                        {
                            "_embedded": {
                                "indexableObject": {
                                    "uuid": "search-item-1",
                                    "name": "Search Result 1",
                                    "handle": "123456789/search1",
                                    "type": "item",
                                    "entityType": "Publication",
                                    "metadata": {
                                        "dc.title": [{"value": "Search Result Title 1"}],
                                        "dc.contributor.author": [{"value": "Author, First"}]
                                    }
                                }
                            }
                        }
                    ]
                }
            }
        },
        "page": {
            "size": 10,
            "totalElements": 1,
            "totalPages": 1,
            "number": 0
        }
    }

@pytest.fixture
def sample_vocabulary_entries():
    """Sample vocabulary entries data"""
    return {
        "_embedded": {
            "vocabularyEntryDetails": [
                {
                    "id": "vocab-entry-1",
                    "display": "Still Image",
                    "value": "still image",
                    "authority": "publication-coar-types:c_ecc8",
                    "selectable": True,
                    "otherInformation": {
                        "note": "A static visual representation",
                        "hasChildren": "false",
                        "id": "c_ecc8"
                    },
                    "_links": {
                        "self": {"href": "http://localhost:8080/server/api/submission/vocabularyEntryDetails/vocab-entry-1"}
                    }
                }
            ]
        }
    }

@pytest.fixture
def temp_test_file(tmp_path):
    """Create a temporary test file for upload tests"""
    test_file = tmp_path / "test_upload.txt"
    test_file.write_text("This is test content for file upload")
    return str(test_file)

@pytest.fixture
def responses_mock():
    """Activate responses mock for HTTP requests"""
    with responses.RequestsMock() as rsps:
        yield rsps


# ---------------------------------------------------------------------------
# Real-session fixtures for managers that go through session.request()
# (BundleManager, PermissionManager). HTTP is intercepted by `responses`,
# so the real requests stack runs: multipart encoding, headers, query strings.
# ---------------------------------------------------------------------------

API_URL = "http://localhost:8080/server/api"


@pytest.fixture
def http_auth(dspace_config):
    """DSpaceAuth with a real requests.Session, marked as authenticated"""
    auth = DSpaceAuth(dspace_config)
    auth._authenticated = True
    return auth


class StreamingRequestsMock(responses.RequestsMock):
    """
    Like a real connection, read streamed (iterable) request bodies before
    answering, so generator bodies run (checksums, progress) and tests can
    inspect request.body as bytes.
    """

    def _on_request(self, adapter, request, **kwargs):
        body = request.body
        if body is not None and not isinstance(body, (bytes, str)) and hasattr(body, "__iter__"):
            request.body = b"".join(body)
        return super()._on_request(adapter, request, **kwargs)


@pytest.fixture
def http():
    """responses mock that does not complain about registered-but-unused URLs"""
    with StreamingRequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def api_url():
    """Build absolute API URLs: api_url('/core/items/x') -> http://.../server/api/core/items/x"""
    return lambda path="": API_URL + path


def hal(key, objects, total_pages=1):
    """A HAL collection page as DSpace returns it"""
    return {"_embedded": {key: objects}, "page": {"totalPages": total_pages, "size": 100, "number": 0}}


@pytest.fixture
def hal_page():
    return hal

# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )

# Custom assertion helpers
class APITestHelpers:
    """Helper methods for API testing"""

    @staticmethod
    def assert_valid_uuid(uuid_string):
        """Assert that a string is a valid UUID"""
        import re
        uuid_pattern = re.compile(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            re.IGNORECASE
        )
        assert uuid_pattern.match(uuid_string), f"Invalid UUID format: {uuid_string}"

    @staticmethod
    def assert_api_call_made(mock_session, method, url_contains):
        """Assert that an API call was made"""
        method_mock = getattr(mock_session, method.lower())
        assert method_mock.called, f"Expected {method} call was not made"

        # Check if any call contains the expected URL part
        calls = method_mock.call_args_list
        url_found = any(url_contains in str(call) for call in calls)
        assert url_found, f"Expected URL containing '{url_contains}' not found in {method} calls"

    @staticmethod
    def mock_api_response(mock_session, method, status_code=200, json_data=None, text_data=None):
        """Configure mock session to return specific response"""
        mock_response = Mock()
        mock_response.status_code = status_code
        if json_data:
            mock_response.json.return_value = json_data
        if text_data:
            mock_response.text = text_data

        method_mock = getattr(mock_session, method.lower())
        method_mock.return_value = mock_response
        return mock_response

@pytest.fixture
def api_helpers():
    """Provide API testing helper methods"""
    return APITestHelpers()

# Auto-use fixtures for common setup
@pytest.fixture(autouse=True)
def setup_logging():
    """Setup logging for tests"""
    import logging
    logging.basicConfig(level=logging.DEBUG)

@pytest.fixture
def error_responses():
    """Common error response data"""
    return {
        "bad_request": {
            "status_code": 400,
            "json": {"error": "Bad Request", "message": "Invalid request data"}
        },
        "unauthorized": {
            "status_code": 401,
            "json": {"error": "Unauthorized", "message": "Authentication required"}
        },
        "forbidden": {
            "status_code": 403,
            "json": {"error": "Forbidden", "message": "Insufficient permissions"}
        },
        "not_found": {
            "status_code": 404,
            "json": {"error": "Not Found", "message": "Resource not found"}
        },
        "server_error": {
            "status_code": 500,
            "json": {"error": "Internal Server Error", "message": "Server error occurred"}
        }
    }
