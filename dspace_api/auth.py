# -*- coding: UTF-8 -*-
"""
Authentication and session management for DSpace API
"""

import base64
import datetime
import json
import logging
import threading
import time
from contextlib import nullcontext

import requests

from .config import DSpaceConfig, Endpoints
from .exceptions import AuthenticationError, SessionError, handle_response_error
from .session import DSpaceSession

# Logging setup for the module
logger = logging.getLogger(__name__)


def jwt_expiry(authorization):
    """
    Expiry (unix time) of a 'Bearer <jwt>' header value, or None if it is
    not a JWT or carries no 'exp'. The signature is not verified - this is
    only used to decide when to refresh our own token.

    Parameters:
        authorization: The 'Authorization' header value (e.g., 'Bearer <jwt>').

    Returns:
        The expiry time as a float (unix time) or None if not available.
    """
    if not authorization:
        return None
    token = authorization.split(" ", 1)[1] if " " in authorization else authorization
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(exp) if exp is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


class DSpaceAuth:
    """Handles authentication and session management for DSpace API"""

    def __init__(self, config: DSpaceConfig):
        """Initialize the DSpaceAuth instance with the given configuration.

        Parameters:
            config: The DSpaceConfig instance containing API configuration.
        """
        self.config = config
        self.session = self._new_session()
        self._authenticated = False
        # Kept in memory only, to log in again when the token expires on long runs
        self._username = None
        self._password = None
        self._token_expires_at = None
        self._reauth_lock = threading.RLock()

    def _new_session(self):
        """Create a new DSpace session.

        Returns:
            DSpaceSession: A new session instance.
        """
        return DSpaceSession(self)

    def _internal(self):
        """Scope for the auth's own requests (no refresh / re-login inside them).

        Returns:
            Context manager for internal requests.
        """
        if isinstance(self.session, DSpaceSession):
            return self.session.internal()
        return nullcontext()

    @property
    def token_expires_at(self):
        """Unix time at which the current login token expires (None if unknown).

        Returns:
            float: The expiry time as a float (unix time) or None if not available.
        """
        return self._token_expires_at

    def _set_authorization(self, value):
        """Set the authorization header for the session and update the token expiry.

        Parameters:
            value: The 'Authorization' header value (e.g., 'Bearer <jwt>').
        """
        self.session.headers.update({"Authorization": value})
        self._token_expires_at = jwt_expiry(value)
        if self._token_expires_at:
            logger.debug("Login token valid until %s",
                         datetime.datetime.fromtimestamp(self._token_expires_at).isoformat())

    @property
    def is_authenticated(self):
        """Check if the session is currently authenticated.

        Returns:
            bool: True if authenticated, False otherwise.
        """
        return self._authenticated

    def create_session(self, username=None, password=None):
        """
        Create and authenticate a session

        Args:
            username: DSpace username
            password: DSpace password

        Returns:
            requests.Session: Authenticated session
        """
        if username and password:
            self.login(username, password)
        else:
            self._setup_csrf_token()

        return self.session

    def check_status(self):
        """
        Check the authentication status of the current session

        Returns:
            bool: True if authenticated, False otherwise
        """
        logger.info("Checking authentication status")

        status_url = self.config.dspace_url + Endpoints.AUTHN_STATUS

        try:
            with self._internal():
                response = self.session.get(status_url, timeout=self.config.timeout)
            self._update_token(response)

            if response.status_code == 200:
                data = response.json()
                authenticated = data.get("authenticated", False)

                if authenticated:
                    logger.info("User is authenticated")
                    self._authenticated = True
                    return True
                else:
                    logger.info("User is not authenticated")
                    self._setup_csrf_token()
                    return False
            else:
                handle_response_error(response, "Status check")

        except requests.exceptions.RequestException as e:
            raise SessionError(f"Failed to check session status: {e}")

    def _setup_csrf_token(self):
        """
        Setup CSRF token for the session

        Parameters:
            None

        Returns:
            None
        """
        csrf_url = self.config.dspace_url + Endpoints.CSRF

        try:
            with self._internal():
                response = self.session.get(csrf_url, timeout=self.config.timeout)
            logger.debug("CSRF status code: %s", response.status_code)

            if "DSPACE-XSRF-TOKEN" in response.headers:
                token = response.headers["DSPACE-XSRF-TOKEN"]
                logger.debug("Setting XSRF token: %s...", token[:6])
                self.session.headers.update({"X-XSRF-Token": token})
                self.session.cookies.update({"X-XSRF-Token": token})
            else:
                logger.warning("No XSRF token found in response headers")

        except requests.exceptions.RequestException as e:
            raise SessionError(f"Failed to setup CSRF token: {e}")

    def login(self, username, password):
        """
        Authenticate with DSpace

        Args:
            username: DSpace username
            password: DSpace password

        Raises:
            AuthenticationError: If login fails
        """
        if not username or not password:
            raise AuthenticationError("Username and password are required")

        # First check status to get CSRF token if needed
        self.check_status()

        login_url = self.config.dspace_url + Endpoints.AUTHN_LOGIN
        login_data = {"user": username, "password": password}

        try:
            with self._internal():
                response = self.session.post(
                    login_url,
                    data=login_data,
                    timeout=self.config.timeout
                )

            self._update_token(response)

            logger.info("Login attempt status: %s", response.status_code)
            logger.info("Login time: %s", datetime.datetime.now())

            if response.status_code == 200:
                logger.info("Login successful")
                self._authenticated = True
                if getattr(self.config, "reauthenticate", True):
                    self._username, self._password = username, password

                # Update session with authorization header if present
                if "Authorization" in response.headers:
                    self._set_authorization(response.headers.get("Authorization"))
            else:
                self._handle_login_error(response)

        except requests.exceptions.RequestException as e:
            raise AuthenticationError(f"Login request failed: {e}")

    def _handle_login_error(self, response):
        """Handle login error responses

        Parameters:
            response: The HTTP response object from the login attempt.

        Raises:
            AuthenticationError: If the login failed due to invalid credentials or forbidden access.
        """
        if response.status_code == 401:
            raise AuthenticationError("Invalid credentials")
        elif response.status_code == 403:
            try:
                error_data = response.json()
                error_msg = error_data.get('message', 'Access forbidden')
                raise AuthenticationError(f"Forbidden: {error_msg}")
            except json.JSONDecodeError:
                raise AuthenticationError("Access forbidden")
        else:
            handle_response_error(response, "Login")

    def _update_token(self, response):
        """Update XSRF token if present in response headers
        
        Parameters:
            response: The HTTP response object from which to extract the XSRF token.

        Returns:
            None
        """
        if "DSPACE-XSRF-TOKEN" in response.headers:
            token = response.headers["DSPACE-XSRF-TOKEN"]
            logger.debug("Updating XSRF token: %s...", token[:6])
            self.session.headers.update({"X-XSRF-Token": token})
            self.session.cookies.update({"X-XSRF-Token": token})

    def logout(self):
        """
        Logout and clean up the session

        Parameters:
            None

        Returns:
            None
        """
        if not self._authenticated:
            logger.info("Session not authenticated, skipping logout")
            return

        logout_url = self.config.dspace_url + Endpoints.AUTHN_LOGOUT

        try:
            with self._internal():
                response = self.session.post(logout_url, timeout=self.config.timeout)

            logger.info("Logout status: %s", response.status_code)
            logger.info("Logout time: %s", datetime.datetime.now())

            if response.status_code == 204:
                logger.info("Logout successful")
                self._cleanup_session()
            else:
                logger.warning("Logout returned status %s", response.status_code)
                self._cleanup_session()  # Clean up anyway

        except requests.exceptions.RequestException as e:
            logger.error("Logout request failed: %s", e)
            self._cleanup_session()  # Clean up anyway

    def _cleanup_session(self):
        """Clean up session headers and close session

        Parameters:
            None

        Returns:
            None
        """
        # Remove auth-related headers
        headers_to_remove = ["Authorization", "X-XSRF-Token"]
        for header in headers_to_remove:
            self.session.headers.pop(header, None)

        # Remove auth-related cookies
        cookies_to_remove = ["X-XSRF-Token"]
        for cookie in cookies_to_remove:
            self.session.cookies.pop(cookie, None)

        self._authenticated = False
        self._username = self._password = None
        self._token_expires_at = None
        self.session.close()

        # Create new session for potential reuse
        self.session = self._new_session()

    # ------------------------------------------------------------------
    # Keeping the login alive on long runs
    # ------------------------------------------------------------------

    def can_reauthenticate(self):
        """Check if the session can reauthenticate.

        Returns:
            bool: True if reauthentication is possible, False otherwise.
        """
        return bool(self._username and self._password and getattr(self.config, "reauthenticate", True))

    def ensure_fresh_token(self):
        """Refresh the token if it expires within config.token_refresh_margin seconds.

        Parameters:
            None

        Returns:
            None
        """
        expires = self._token_expires_at
        if not self._authenticated or expires is None:
            return
        margin = getattr(self.config, "token_refresh_margin", 60)
        if time.time() < expires - margin:
            return
        with self._reauth_lock:
            # another thread may have refreshed while we waited for the lock
            if self._token_expires_at is not None and time.time() < self._token_expires_at - margin:
                return
            self.refresh_token()

    def refresh_token(self):
        """
        Get a new token. DSpace issues one for a POST to /authn/login that
        carries a still-valid token; if that fails, log in again.

        Parameters:
            None

        Returns:
            bool: True if the token was successfully refreshed or reauthenticated, False otherwise.
        """
        login_url = self.config.dspace_url + Endpoints.AUTHN_LOGIN
        try:
            with self._internal():
                response = self.session.post(login_url, timeout=self.config.timeout)
            self._update_token(response)
            if response.status_code == 200 and "Authorization" in response.headers:
                self._set_authorization(response.headers["Authorization"])
                logger.info("Login token refreshed")
                return True
            logger.info("Token refresh returned %s - logging in again", response.status_code)
        except requests.exceptions.RequestException as e:
            logger.warning("Token refresh failed (%s) - logging in again", e)
        return self.reauthenticate()

    # ------------------------------------------------------------------
    # DSpace-CRIS end-user agreement
    # ------------------------------------------------------------------

    # DSpace-CRIS end-user agreement field in the eperson metadata
    AGREEMENT_FIELD = "dspace.agreements.end-user"

    def accept_user_agreement(self):
        """
        Accept the DSpace-CRIS end-user agreement for the logged-in user and
        get a new token (the server reads the flag from a token claim).
        Returns True if the agreement is accepted afterwards.

        Parameters:
            None

        Returns:
            bool: True if the agreement is accepted successfully, False otherwise.
        """
        with self._reauth_lock:
            try:
                with self._internal():
                    status = self.session.get(self.config.dspace_url + Endpoints.AUTHN_STATUS,
                                              params={"embed": "eperson"}, timeout=self.config.timeout)
                    self._update_token(status)
                    person = ((status.json() if status.status_code == 200 else {}).get("_embedded") or {}
                              ).get("eperson") or {}
                    if not person.get("uuid"):
                        logger.warning("Cannot accept the user agreement: no logged-in eperson")
                        return False
                    values = (person.get("metadata") or {}).get(self.AGREEMENT_FIELD) or []
                    if not any(str(v.get("value")).lower() == "true" for v in values):
                        operation = {"op": "replace" if values else "add",
                                     "path": f"/metadata/{self.AGREEMENT_FIELD}",
                                     "value": [{"value": "true"}]}
                        patch = self.session.patch(
                            f"{self.config.dspace_url}{Endpoints.EPERSONS}/{person['uuid']}",
                            json=[operation], headers=self.config.headers, timeout=self.config.timeout)
                        self._update_token(patch)
                        if patch.status_code != 200:
                            logger.warning("Accepting the user agreement failed: HTTP %s %s",
                                           patch.status_code, patch.text[:200])
                            return False
                        logger.info("End-user agreement accepted for %s", person.get("email") or person["uuid"])
            except (requests.exceptions.RequestException, ValueError) as e:
                logger.warning("Accepting the user agreement failed: %s", e)
                return False
            # new token -> new userAgreementAccepted claim
            return self.refresh_token()

    def reauthenticate(self):
        """Log in again with the stored credentials (used after expiry or a 401).
        
        Parameters:
            None

        Returns:
            bool: True if reauthentication is successful, False otherwise.
        """
        if not self.can_reauthenticate():
            raise AuthenticationError("Session expired and no stored credentials to log in again")
        with self._reauth_lock:
            # Drop the dead token, otherwise /authn/status itself may answer 401
            self.session.headers.pop("Authorization", None)
            self._token_expires_at = None
            self._authenticated = False
            self.login(self._username, self._password)
            logger.info("Logged in again")
            return True
