"""
Tests for DSpace authentication module
"""

from unittest.mock import Mock, patch
import pytest
import requests
import json
from dspace_api.auth import DSpaceAuth
from dspace_api.exceptions import AuthenticationError, SessionError, ServerError


class TestDSpaceAuth:
    """Test cases for DSpace authentication"""

    def test_init(self, dspace_config):
        """Test DSpaceAuth initialization"""
        auth = DSpaceAuth(dspace_config)

        assert auth.config == dspace_config
        assert isinstance(auth.session, requests.Session)
        assert not auth.is_authenticated

    def test_create_session_without_credentials(self, dspace_config, mock_responses):
        """Test creating session without credentials"""
        auth = DSpaceAuth(dspace_config)

        # Mock CSRF endpoint
        mock_responses.auth_status_unauthenticated["authenticated"] = False
        auth.session.get = Mock(return_value=Mock(
            status_code=200,
            json=Mock(return_value=mock_responses.auth_status_unauthenticated),
            headers={"DSPACE-XSRF-TOKEN": "test-csrf-token"}
        ))

        session = auth.create_session()

        assert session == auth.session
        assert not auth.is_authenticated

    def test_create_session_with_credentials(self, dspace_config, mock_responses):
        """Test creating session with credentials"""
        auth = DSpaceAuth(dspace_config)

        # Mock status check (unauthenticated)
        status_mock = Mock(
            status_code=200,
            json=Mock(return_value=mock_responses.auth_status_unauthenticated),
            headers={"DSPACE-XSRF-TOKEN": "csrf-token"}
        )

        # Mock login success
        login_mock = Mock(
            status_code=200,
            headers={"Authorization": "Bearer test-token", "DSPACE-XSRF-TOKEN": "new-csrf"}
        )

        auth.session.get = Mock(return_value=status_mock)
        auth.session.post = Mock(return_value=login_mock)

        session = auth.create_session("testuser", "testpass")

        assert session == auth.session
        assert auth.is_authenticated
        assert auth.session.headers.get("Authorization") == "Bearer test-token"

    def test_check_status_authenticated(self, dspace_config, mock_responses):
        """Test status check when authenticated"""
        auth = DSpaceAuth(dspace_config)

        mock_response = Mock(
            status_code=200,
            json=Mock(return_value=mock_responses.auth_status_authenticated),
            headers={"DSPACE-XSRF-TOKEN": "token123"}
        )

        auth.session.get = Mock(return_value=mock_response)

        result = auth.check_status()

        assert result is True
        assert auth.is_authenticated

    def test_check_status_unauthenticated(self, dspace_config, mock_responses):
        """Test status check when not authenticated"""
        auth = DSpaceAuth(dspace_config)

        # Mock status response (unauthenticated)
        status_mock = Mock(
            status_code=200,
            json=Mock(return_value=mock_responses.auth_status_unauthenticated),
            headers={"DSPACE-XSRF-TOKEN": "csrf-token"}
        )

        # Mock CSRF response
        csrf_mock = Mock(
            status_code=200,
            headers={"DSPACE-XSRF-TOKEN": "csrf-token"}
        )

        auth.session.get = Mock(side_effect=[status_mock, csrf_mock])

        result = auth.check_status()

        assert result is False
        assert not auth.is_authenticated
        assert auth.session.headers.get("X-XSRF-Token") == "csrf-token"

    def test_check_status_error_response(self, dspace_config):
        """Test status check with error response"""
        auth = DSpaceAuth(dspace_config)

        auth.session.get = Mock(return_value=Mock(
            status_code=500,
            text="Server error",
            headers={}
        ))

        with pytest.raises(ServerError):
            auth.check_status()

    def test_login_success(self, dspace_config, mock_responses):
        """Test successful login"""
        auth = DSpaceAuth(dspace_config)

        # Mock check_status to return False (not authenticated)
        auth.check_status = Mock(return_value=False)

        # Mock successful login
        login_mock = Mock(
            status_code=200,
            headers={"Authorization": "Bearer token123", "DSPACE-XSRF-TOKEN": "csrf"}
        )

        auth.session.post = Mock(return_value=login_mock)
        auth._update_token = Mock()

        auth.login("testuser", "testpass")

        assert auth.is_authenticated
        auth.session.post.assert_called_once()
        auth._update_token.assert_called_once_with(login_mock)

    def test_login_invalid_credentials(self, dspace_config):
        """Test login with invalid credentials"""
        auth = DSpaceAuth(dspace_config)
        auth.check_status = Mock(return_value=False)

        auth.session.post = Mock(return_value=Mock(
            status_code=401,
            headers={}  # Add headers
        ))

        with pytest.raises(AuthenticationError, match="Invalid credentials"):
            auth.login("baduser", "badpass")

    def test_login_forbidden(self, dspace_config):
        """Test login with forbidden response"""
        auth = DSpaceAuth(dspace_config)
        auth.check_status = Mock(return_value=False)

        auth.session.post = Mock(return_value=Mock(
            status_code=403,
            json=Mock(return_value={"message": "Account locked"}),
            headers={}  # Add empty headers dict
        ))

        with pytest.raises(AuthenticationError, match="Forbidden"):
            auth.login("lockeduser", "password")

    def test_login_missing_credentials(self, dspace_config):
        """Test login with missing credentials"""
        auth = DSpaceAuth(dspace_config)

        with pytest.raises(AuthenticationError, match="Username and password are required"):
            auth.login(None, "password")

        with pytest.raises(AuthenticationError, match="Username and password are required"):
            auth.login("user", None)

    def test_login_request_exception(self, dspace_config):
        """Test login with request exception"""
        auth = DSpaceAuth(dspace_config)
        auth.check_status = Mock(return_value=False)

        auth.session.post = Mock(side_effect=requests.exceptions.ConnectionError("Network error"))

        with pytest.raises(AuthenticationError, match="Login request failed"):
            auth.login("user", "pass")

    def test_update_token(self, dspace_config):
        """Test token update from response headers"""
        auth = DSpaceAuth(dspace_config)

        response = Mock(headers={"DSPACE-XSRF-TOKEN": "new-token"})

        auth._update_token(response)

        assert auth.session.headers.get("X-XSRF-Token") == "new-token"
        assert auth.session.cookies.get("X-XSRF-Token") == "new-token"

    def test_update_token_no_token(self, dspace_config):
        """Test token update when no token in headers"""
        auth = DSpaceAuth(dspace_config)
        original_headers = auth.session.headers.copy()

        response = Mock(headers={})

        auth._update_token(response)

        # Headers should remain unchanged
        assert auth.session.headers == original_headers

    def test_setup_csrf_token_success(self, dspace_config):
        """Test CSRF token setup"""
        auth = DSpaceAuth(dspace_config)

        csrf_mock = Mock(
            status_code=200,
            headers={"DSPACE-XSRF-TOKEN": "csrf-123"}
        )

        auth.session.get = Mock(return_value=csrf_mock)

        auth._setup_csrf_token()

        assert auth.session.headers.get("X-XSRF-Token") == "csrf-123"

    def test_setup_csrf_token_missing(self, dspace_config):
        """Test CSRF token setup when token is missing"""
        auth = DSpaceAuth(dspace_config)

        csrf_mock = Mock(status_code=200, headers={})
        auth.session.get = Mock(return_value=csrf_mock)

        # Should not raise exception, just log warning
        auth._setup_csrf_token()

    def test_setup_csrf_token_request_failure(self, dspace_config):
        """Test CSRF token setup with request failure"""
        auth = DSpaceAuth(dspace_config)

        auth.session.get = Mock(side_effect=requests.exceptions.Timeout("Timeout"))

        with pytest.raises(SessionError, match="Failed to setup CSRF token"):
            auth._setup_csrf_token()

    def test_logout_success_cleans_session(self, dspace_config):
        """Test successful logout when authenticated cleans session"""
        auth = DSpaceAuth(dspace_config)
        auth._authenticated = True

        old_session = auth.session
        old_session.post = Mock(return_value=Mock(status_code=204))
        old_session.close = Mock()

        auth.logout()

        old_session.post.assert_called_once()
        old_session.close.assert_called_once()
        assert not auth.is_authenticated
        assert auth.session is not old_session

    def test_logout_not_authenticated(self, dspace_config):
        """Test logout when not authenticated"""
        auth = DSpaceAuth(dspace_config)
        auth._authenticated = False

        auth.session.post = Mock()

        auth.logout()

        # Should not make API call
        auth.session.post.assert_not_called()

    def test_logout_error_response(self, dspace_config):
        """Test logout with error response"""
        auth = DSpaceAuth(dspace_config)
        auth._authenticated = True

        logout_mock = Mock(status_code=500)
        auth.session.post = Mock(return_value=logout_mock)
        auth.session.close = Mock()

        # Should not raise exception, just clean up
        auth.logout()

        assert not auth.is_authenticated

    def test_logout_request_exception(self, dspace_config):
        """Test logout with request exception"""
        auth = DSpaceAuth(dspace_config)
        auth._authenticated = True

        auth.session.post = Mock(side_effect=requests.exceptions.ConnectionError("Network error"))
        auth.session.close = Mock()

        # Should not raise exception, just clean up
        auth.logout()

        assert not auth.is_authenticated

    def test_cleanup_session(self, dspace_config):
        """Test session cleanup"""
        auth = DSpaceAuth(dspace_config)
        auth._authenticated = True

        # Set up session with headers and cookies
        auth.session.headers.update({
            "Authorization": "Bearer token",
            "X-XSRF-Token": "csrf-token"
            # Remove Content-Type assertion as it's not guaranteed
        })
        auth.session.cookies.update({"X-XSRF-Token": "csrf-token"})

        original_session = auth.session
        original_session.close = Mock()

        auth._cleanup_session()

        # Check headers are cleaned
        assert "Authorization" not in auth.session.headers
        assert "X-XSRF-Token" not in auth.session.headers

        # Check cookies are cleaned
        assert "X-XSRF-Token" not in auth.session.cookies

        # Check session is closed and new one created
        original_session.close.assert_called_once()
        assert auth.session != original_session
        assert not auth.is_authenticated

    def test_handle_login_error_json_response(self, dspace_config):
        """Test login error handling with JSON response"""
        auth = DSpaceAuth(dspace_config)

        response = Mock(
            status_code=403,
            json=Mock(return_value={"message": "Account suspended"})
        )

        with pytest.raises(AuthenticationError, match="Forbidden: Account suspended"):
            auth._handle_login_error(response)

    def test_handle_login_error_invalid_json(self, dspace_config):
        """Test login error handling with invalid JSON"""
        auth = DSpaceAuth(dspace_config)

        response = Mock(
            status_code=403,
            json=Mock(side_effect=json.JSONDecodeError("Invalid JSON", "", 0))  # Fix: Use JSONDecodeError
        )

        with pytest.raises(AuthenticationError, match="Access forbidden"):
            auth._handle_login_error(response)

    def test_handle_login_error_server_error(self, dspace_config):
        """Test login error handling with server error"""
        auth = DSpaceAuth(dspace_config)

        response = Mock(status_code=500, text="Internal server error")

        with pytest.raises(Exception):  # Should use generic error handler
            auth._handle_login_error(response)

    def test_check_status_request_exception(self, dspace_config):
        """Test status check raises SessionError on request exception"""
        auth = DSpaceAuth(dspace_config)

        # Simulate a network error
        auth.session.get = Mock(side_effect=requests.exceptions.ConnectionError("Network down"))

        with pytest.raises(SessionError, match="Failed to check session status:"):
            auth.check_status()

    @patch('dspace_api.auth.DSpaceSession')
    def test_session_recreation_after_cleanup(self, mock_session_class, dspace_config):
        """Test that a new session is created after cleanup"""
        # Create different mock instances for original and new session
        original_mock = Mock()
        new_mock = Mock()
        mock_session_class.side_effect = [original_mock, new_mock]

        auth = DSpaceAuth(dspace_config)
        original_session = auth.session

        auth._cleanup_session()

        # Should create new session
        assert auth.session != original_session
        assert mock_session_class.call_count == 2

    def test_is_authenticated_property(self, dspace_config):
        """Test is_authenticated property"""
        auth = DSpaceAuth(dspace_config)

        assert not auth.is_authenticated

        auth._authenticated = True
        assert auth.is_authenticated

        auth._authenticated = False
        assert not auth.is_authenticated


    def test_login_success_without_authorization_header(self, dspace_config):
        """Test login succeeds and leaves headers alone when no Authorization header is returned"""
        auth = DSpaceAuth(dspace_config)
        auth.check_status = Mock(return_value=False)
        auth.session.post = Mock(return_value=Mock(status_code=200, headers={}))

        auth.login("user", "pass")

        assert auth.is_authenticated
        assert "Authorization" not in auth.session.headers
