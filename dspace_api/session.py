# -*- coding: UTF-8 -*-
"""
Resilient HTTP session for the DSpace API toolkit.

DSpaceAuth creates a DSpaceSession; every manager uses it through
``auth.session`` - including the older managers that call ``session.get()``
directly - so all requests get the same behaviour:

- Transient failures are retried with exponential backoff:
    - HTTP 429 (any method - the server did not process the request),
      honouring Retry-After
    - HTTP 502/503/504 (configurable) for idempotent methods only
    - connection errors and read timeouts for idempotent methods only
    - connect timeouts for any method (the request never reached the server)
  POST and PATCH are NOT retried after a 5xx or a broken connection,
  because DSpace may already have created the bitstream / applied the patch.

- The login token (JWT) is refreshed shortly before it expires, and a 401
  triggers one re-login with the stored credentials and one retry.

- A 403 that carries a fresh CSRF token is retried once (non-GET only).

- Optional projections (allLanguages, preventMetadataSecurity,
  preventPointsCalculation) exist only on some servers (DSpace-CRIS,
  DSpace 10). Plain DSpace 8/9 answer an unknown projection with 400/404.
  On such an answer the session checks once per projection whether the
  server knows it (GET /core/sites?projection=X), remembers the result,
  drops unsupported ones from this and every later request and resends.

- Metadata PATCH on archived objects: DSpace uses only the FIRST value of
  an array in "add /metadata/<field>" and "replace /metadata/<field>". An
  operation with several values is sent as "remove" (replace only) plus
  one "add /metadata/<field>/-" per value, so no value is lost silently.

- DSpace-CRIS answers every request of a user who has not accepted the
  end-user agreement with 403 "No user agreement accepted". With
  config.accept_user_agreement the agreement is accepted for the logged-in
  user, the token is refreshed (the flag is a token claim) and the request
  is sent again; otherwise the 403 surfaces as UserAgreementRequired.

Requests made by DSpaceAuth itself (login, status, CSRF, logout) run in an
"internal" scope: they are retried on transient errors but never trigger a
token refresh or re-login, which would recurse.

Bodies are re-sent on retry: pass bytes / strings / dicts (as the toolkit
does), not one-shot generators or open file objects.
"""

import email.utils
import json
import logging
import random
import threading
import time
import weakref
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .config import DEFAULT_RETRY_STATUSES

# Logger for the module
logger = logging.getLogger(__name__)

# Constants for session management and DSpace API behavior

# XSRF token header used by DSpace
XSRF_RESPONSE_HEADER = "DSPACE-XSRF-TOKEN"
# HTTP methods considered idempotent by the session manager
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
# The server did not process the request -> safe to retry any method
ALWAYS_RETRYABLE_STATUSES = frozenset({429})

# Projections not every server has (see module docstring)
OPTIONAL_PROJECTIONS = frozenset({"allLanguages", "preventMetadataSecurity", "preventPointsCalculation"})
UNKNOWN_PROJECTION_STATUSES = frozenset({400, 404})
# Message indicating that the user has not accepted the end-user agreement
USER_AGREEMENT_MESSAGE = "No user agreement accepted"


def _projection_values(value):
    """Normalize projection values into a list of names."""
    values = value if isinstance(value, (list, tuple)) else [value]
    names = []
    for v in values:
        names.extend(n.strip() for n in str(v).split(",") if n.strip())
    return names


def requested_projections(url, params):
    """Names of the projections in the URL query string and in `params`."""
    names = [n for key, value in parse_qsl(urlsplit(url).query) if key == "projection"
             for n in _projection_values(value)]
    if isinstance(params, dict):
        names.extend(_projection_values(params["projection"]) if params.get("projection") is not None else [])
    elif isinstance(params, (list, tuple)):
        for pair in params:
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] == "projection":
                names.extend(_projection_values(pair[1]))
    return names


def _keep(value, drop):
    """Keep only the projection values not in `drop`."""
    kept = [n for n in _projection_values(value) if n not in drop]
    return ",".join(kept) if kept else None


def strip_projections(url, params, drop):
    """(url, params) without the projections in `drop` (same params shape)."""
    parts = urlsplit(url)
    if parts.query:
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key == "projection":
                value = _keep(value, drop)
                if value is None:
                    continue
            query.append((key, value))
        url = urlunsplit(parts._replace(query=urlencode(query)))
    if isinstance(params, dict):
        params = dict(params)
        if params.get("projection") is not None:
            values = params["projection"] if isinstance(params["projection"], (list, tuple)) else [params["projection"]]
            kept = [k for k in (_keep(v, drop) for v in values) if k is not None]
            if kept:
                params["projection"] = kept if len(kept) > 1 else kept[0]
            else:
                del params["projection"]
    elif isinstance(params, (list, tuple)):
        cleaned = []
        for pair in params:
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] == "projection":
                value = _keep(pair[1], drop)
                if value is None:
                    continue
                pair = (pair[0], value)
            cleaned.append(pair)
        params = cleaned
    return url, params


def _is_value_list(value):
    """Check if the value is a list with more than one element."""
    return isinstance(value, list) and len(value) > 1


def expand_metadata_operations(operations):
    """
    Rewrite multi-value 'add'/'replace' on /metadata/<field> into operations
    DSpace applies completely (see module docstring). Other operations, and
    single values, are returned unchanged. Returns None if nothing changed.

    Parameters:
        operations: List of metadata operations to expand

    Returns:
        list: Expanded list of operations, or None if no changes were made
    """
    if not isinstance(operations, list):
        return None
    result, changed = [], False
    for op in operations:
        path = op.get("path") if isinstance(op, dict) else None
        if (isinstance(path, str) and path.startswith("/metadata/") and path.count("/") == 2
                and op.get("op") in ("add", "replace") and _is_value_list(op.get("value"))):
            if op["op"] == "replace":
                result.append({"op": "remove", "path": path})
            result.extend({"op": "add", "path": f"{path}/-", "value": value} for value in op["value"])
            changed = True
        else:
            result.append(op)
    return result if changed else None


def is_user_agreement_response(response):
    """
    Check if the response indicates that the user has not accepted the end-user agreement.

    Parameters:
        response: The HTTP response object to check

    Returns:
        bool: True if the response indicates a missing user agreement, False otherwise
    """
    if response.status_code != 403:
        return False
    try:
        return USER_AGREEMENT_MESSAGE in (response.text or "")
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - defensive
        return False


def parse_retry_after(value, now=None):
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP date), or None.

    Parameters:
        value: The value of the Retry-After header
        now: Current time as a timestamp, used for calculating the wait time

    Returns:
        float: Number of seconds to wait, or None if the header is invalid
    """
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:  # pragma: no cover - older Pythons return None instead of raising
        return None
    now = time.time() if now is None else now
    return max(0.0, when.timestamp() - now)


class _Settings:
    """Retry settings used when the session has no auth/config attached."""
    max_retries = 3         # Maximum number of retry attempts
    retry_backoff = 1.0     # Backoff factor for retry delays
    retry_max_wait = 30.0   # Maximum wait time between retries in seconds
    retry_statuses = frozenset(DEFAULT_RETRY_STATUSES)


class DSpaceSession(requests.Session):
    """requests.Session with retries, token refresh, re-login and CSRF retry"""

    def __init__(self, auth=None):
        """Initialize the DSpaceSession.

        Parameters:
            auth: Optional authentication object to attach to the session
        """
        super().__init__()
        # weakref: the auth object owns the session, avoid a reference cycle
        self._auth_ref = weakref.ref(auth) if auth is not None else None
        self._local = threading.local()
        self.sleep = time.sleep          # replaceable in tests
        # projection name -> True (server knows it) / False (unknown, dropped)
        self.projection_support = {}
        self._projection_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Scope for DSpaceAuth's own requests
    # ------------------------------------------------------------------

    @property
    def dspace_auth(self):
        """Get the current DSpaceAuth instance attached to the session.

        Returns:
            DSpaceAuth or None: The authentication object if attached, None otherwise
        """
        return self._auth_ref() if self._auth_ref is not None else None

    @contextmanager
    def internal(self):
        """Context manager for marking requests as internal to the DSpaceAuth session.

        Yields:
            DSpaceSession: The current session instance
        """
        self._local.depth = getattr(self._local, "depth", 0) + 1
        try:
            yield self
        finally:
            self._local.depth -= 1

    def _is_internal(self):
        """Check if the current request is internal to the DSpaceAuth session.

        Returns:
            bool: True if the request is internal, False otherwise
        """
        return getattr(self._local, "depth", 0) > 0

    def _settings(self):
        """Get the settings for the current session, falling back to default settings if necessary.

        Returns:
            _Settings: The settings object to use for the session
        """
        auth = self.dspace_auth
        config = getattr(auth, "config", None)
        return config if config is not None and hasattr(config, "max_retries") else _Settings

    # ------------------------------------------------------------------
    # Request pipeline
    # ------------------------------------------------------------------

    def request(self, method, url, *args, **kwargs):
        """Send an HTTP request through the DSpace session, handling authentication, retries, and projections.

        Parameters:
            method (str): The HTTP method (e.g., "GET", "POST", "PATCH")
            url (str): The URL to send the request to
            *args: Additional positional arguments to pass to the request
            **kwargs: Additional keyword arguments to pass to the request

        Returns:
            Response: The HTTP response object
        """
        auth = self.dspace_auth
        if auth is None or self._is_internal():
            return self._request_with_retries(method, url, args, kwargs)

        auth.ensure_fresh_token()
        is_get = method.upper() == "GET"
        if is_get:
            url, kwargs = self._without_unsupported_projections(url, kwargs)
        elif method.upper() == "PATCH":
            kwargs = self._expand_metadata_patch(kwargs)
        response = self._send(auth, method, url, args, kwargs)

        if is_get and response.status_code in UNKNOWN_PROJECTION_STATUSES:
            retry = self._drop_unknown_projections(auth, url, kwargs)
            if retry is not None:
                response.close()
                url, kwargs = retry
                response = self._send(auth, method, url, args, kwargs)

        # DSpace answers a stale CSRF token with 403 + a fresh token
        if (response.status_code == 403 and method.upper() != "GET"
                and XSRF_RESPONSE_HEADER in response.headers):
            logger.debug("Retrying %s %s with refreshed CSRF token", method, url)
            response = self._send(auth, method, url, args, kwargs)

        # Expired / revoked login: log in again once and repeat the request
        if response.status_code == 401 and auth.can_reauthenticate():
            logger.warning("%s %s returned 401 - logging in again and retrying once", method, url)
            auth.reauthenticate()
            response = self._send(auth, method, url, args, kwargs)

        # DSpace-CRIS: the logged-in user has not accepted the end-user agreement
        if (is_user_agreement_response(response) and getattr(auth.config, "accept_user_agreement", False)
                and getattr(auth, "is_authenticated", False)):
            logger.warning("%s %s: no user agreement accepted - accepting it for the logged-in user "
                           "(accept_user_agreement is on) and retrying once", method, url)
            if auth.accept_user_agreement():
                response.close()
                response = self._send(auth, method, url, args, kwargs)

        return response

    # ------------------------------------------------------------------
    # Optional projections and metadata PATCH
    # ------------------------------------------------------------------

    def _without_unsupported_projections(self, url, kwargs):
        """Remove projections from the request that are known to be unsupported by the server.

        Parameters:
            url (str): The request URL
            kwargs (dict): The request keyword arguments, including "params"

        Returns:
            tuple: A tuple of (url, kwargs) with unsupported projections removed
        """
        unsupported = {name for name, ok in self.projection_support.items() if ok is False}
        if not unsupported:
            return url, kwargs
        params = kwargs.get("params")
        if not unsupported.intersection(requested_projections(url, params)):
            return url, kwargs
        url, params = strip_projections(url, params, unsupported)
        return url, {**kwargs, "params": params}

    def _drop_unknown_projections(self, auth, url, kwargs):
        """Return (url, kwargs) without projections the server turned out not to know, or None if no unknown projections were found.

        Parameters:
            auth: The authentication object to use for checking projection support
            url (str): The request URL
            kwargs (dict): The request keyword arguments, including "params"

        Returns:
            tuple or None: A tuple of (url, kwargs) with unknown projections removed, or None if no unknown projections were found
        """
        candidates = [name for name in dict.fromkeys(requested_projections(url, kwargs.get("params")))
                      if name in OPTIONAL_PROJECTIONS and self.projection_support.get(name) is None]
        unknown = {name for name in candidates if not self.supports_projection(name, auth)}
        if not unknown:
            return None
        url, params = strip_projections(url, kwargs.get("params"), unknown)
        return url, {**kwargs, "params": params}

    def supports_projection(self, name, auth=None):
        """
        Check if the server supports the given projection.

        Whether the server knows projection `name` (checked once with
        GET /core/sites?projection=name, then remembered). Unclear answers
        (e.g. 5xx, network errors) count as supported and are checked again later.

        Parameters:
            name (str): The name of the projection to check
            auth: Optional authentication object to use for the check

        Returns:
            bool: True if the server supports the projection, False otherwise
        """
        known = self.projection_support.get(name)
        if known is not None:
            return known
        auth = auth or self.dspace_auth
        base = getattr(getattr(auth, "config", None), "dspace_url", None)
        if not base:
            return True
        with self._projection_lock:
            if name in self.projection_support:
                return self.projection_support[name]
            try:
                with self.internal():
                    probe = self._request_with_retries("GET", f"{base}/core/sites", (),
                                                       {"params": {"projection": name, "size": 1},
                                                        "timeout": getattr(auth.config, "timeout", 30)})
            except requests.exceptions.RequestException as exc:
                logger.debug("Projection check for %s failed: %s", name, exc)
                return True
            status = probe.status_code
            probe.close()
            if status == 200:
                self.projection_support[name] = True
            elif status in UNKNOWN_PROJECTION_STATUSES:
                self.projection_support[name] = False
                logger.warning("This server does not know projection=%s (DSpace-CRIS / DSpace 10 only) - "
                               "requests are sent without it from now on", name)
            else:
                return True
            return self.projection_support[name]

    @staticmethod
    def _expand_metadata_patch(kwargs):
        """Expand metadata operations in the request payload.

        Parameters:
            kwargs (dict): The request keyword arguments, potentially containing "json" or "data"

        Returns:
            dict: The modified keyword arguments with expanded metadata operations
        """
        if "json" in kwargs:
            expanded = expand_metadata_operations(kwargs["json"])
            return {**kwargs, "json": expanded} if expanded is not None else kwargs
        data = kwargs.get("data")
        if isinstance(data, (str, bytes)) and data[:1] in ("[", b"["):
            try:
                operations = json.loads(data)
            except ValueError:
                return kwargs
            expanded = expand_metadata_operations(operations)
            if expanded is not None:
                return {**kwargs, "data": json.dumps(expanded)}
        return kwargs

    def _send(self, auth, method, url, args, kwargs):
        """Send an HTTP request with authentication and handle token updates.

        Parameters:
            auth: The authentication object to use for the request
            method (str): The HTTP method (e.g., "GET", "POST", "PATCH")
            url (str): The URL to send the request to
            args: Additional positional arguments to pass to the request
            kwargs: Additional keyword arguments to pass to the request

        Returns:
            Response: The HTTP response object
        """
        response = self._request_with_retries(method, url, args, kwargs)
        auth._update_token(response)
        return response

    def _backoff(self, settings, attempt):
        """Calculate the backoff time for a retry attempt.

        Parameters:
            settings (_Settings): The session settings containing retry configuration
            attempt (int): The current retry attempt number

        Returns:
            float: The time to wait before the next retry, in seconds
        """
        base = settings.retry_backoff * (2 ** attempt)
        # up to 10% jitter so parallel workers don't retry in lockstep
        return min(settings.retry_max_wait, base + random.uniform(0, base * 0.1))

    def _request_with_retries(self, method, url, args, kwargs):
        """Send an HTTP request with retries for transient errors.

        Parameters:
            method (str): The HTTP method (e.g., "GET", "POST", "PATCH")
            url (str): The URL to send the request to
            args: Additional positional arguments to pass to the request
            kwargs: Additional keyword arguments to pass to the request

        Returns:
            Response: The HTTP response object

        Raises:
            requests.exceptions.RequestException: If the request fails and is not retryable
        """
        settings = self._settings()
        idempotent = method.upper() in IDEMPOTENT_METHODS
        attempt = 0
        while True:
            try:
                response = super().request(method, url, *args, **kwargs)
            except requests.exceptions.ConnectTimeout as exc:
                error, retryable = exc, True             # never reached the server
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as exc:
                error, retryable = exc, idempotent        # may have been processed
            else:
                status = response.status_code
                retryable = attempt < settings.max_retries and (
                    status in ALWAYS_RETRYABLE_STATUSES
                    or (idempotent and status in settings.retry_statuses))
                if not retryable:
                    return response
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                wait = (min(settings.retry_max_wait, retry_after) if retry_after is not None
                        else self._backoff(settings, attempt))
                logger.warning("%s %s returned %s - retry %d/%d in %.1fs",
                               method, url, status, attempt + 1, settings.max_retries, wait)
                response.close()
                self.sleep(wait)
                attempt += 1
                continue

            if not retryable or attempt >= settings.max_retries:
                raise error
            wait = self._backoff(settings, attempt)
            logger.warning("%s %s failed (%s) - retry %d/%d in %.1fs",
                           method, url, type(error).__name__, attempt + 1, settings.max_retries, wait)
            self.sleep(wait)
            attempt += 1
