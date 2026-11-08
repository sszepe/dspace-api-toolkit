"""
Tests for DSpace API exceptions
"""

import pytest
from unittest.mock import Mock
from dspace_api.exceptions import (
    handle_response_error,
    AuthenticationError,
    AuthorizationError,
    DSpaceError,
)


class TestExceptions:
    """Class-based tests for exceptions."""

    def test_handle_response_error_401_unauthorized(self):
        """Test handling of 401 Unauthorized errors"""
        resp = Mock(status_code=401, text="unauthorized")
        with pytest.raises(AuthenticationError):
            handle_response_error(resp, "Check something")

    def test_handle_response_error_403_forbidden(self):
        """Test handling of 403 Forbidden errors"""
        resp = Mock(status_code=403, text="forbidden")
        with pytest.raises(AuthorizationError):
            handle_response_error(resp, "Do something")

    def test_handle_response_error_unexpected_status(self):
        """Test handling of unexpected status codes"""
        resp = Mock(status_code=418, text="teapot")
        with pytest.raises(DSpaceError) as exc:
            handle_response_error(resp, "Brew coffee")
        # Assert message and captured fields to lock behavior
        assert "Unexpected status 418" in str(exc.value)
        assert exc.value.status_code == 418
        assert exc.value.response_text == "teapot"