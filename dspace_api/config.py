# -*- coding: UTF-8 -*-
"""
Configuration and constants for DSpace API client
"""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from dotenv import find_dotenv, load_dotenv

from .projection import ALL_LANGUAGES, apply_view

# Projection that makes DSpace return metadata values in every language,
# instead of only the values matching the request locale.
ALL_LANGUAGES_PROJECTION = ALL_LANGUAGES

# Scoped override set by DSpaceConfig.languages(); None = use the config default.
# A ContextVar keeps the override local to the current thread / asyncio task.
_all_languages_scope: ContextVar[Optional[bool]] = ContextVar("dspace_all_languages", default=None)

# Environment variable truthy values
_TRUE_VALUES = ("1", "true", "yes", "on")


def _env_bool(name, default=False):
    """Parse an environment variable as a boolean."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in _TRUE_VALUES


def _env_int(name, default):
    """Parse an environment variable as an integer."""
    value = os.getenv(name)
    try:
        return int(value) if value is not None and value.strip() else default
    except ValueError:
        return default


def _env_float(name, default):
    """Parse an environment variable as a float."""
    value = os.getenv(name)
    try:
        return float(value) if value is not None and value.strip() else default
    except ValueError:
        return default


def _env_int_set(name, default):
    """Parse an environment variable as a set of integers."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return frozenset(default)
    try:
        return frozenset(int(v) for v in value.replace(";", ",").split(",") if v.strip())
    except ValueError:
        return frozenset(default)


# Statuses that usually mean "try again later" rather than "your request is wrong"
DEFAULT_RETRY_STATUSES = (429, 502, 503, 504)


def with_all_languages(params, enabled):
    """
    Return `params` with the allLanguages projection applied if `enabled` is True.

    Accepts None, a dict or a list of (key, value) tuples and returns the
    same shape (None becomes a list). An existing projection (e.g.
    preventMetadataSecurity) is kept: DSpace accepts the parameter
    several times and combines the projections.
    """
    return apply_view(params, ALL_LANGUAGES_PROJECTION) if enabled else params


class DSpaceConfig:
    """
    Configuration class for DSpace API settings
    """

    def __init__(self, dspace_url=None, version=8, all_languages=None, timeout=None,
                 max_retries=None, retry_backoff=None, retry_max_wait=None, retry_statuses=None,
                 token_refresh_margin=None, reauthenticate=None, env_file=True,
                 accept_user_agreement=None):
        """
        Robustness settings (each falls back to an environment variable / .env):

            timeout               DSPACE_TIMEOUT                seconds per request (30)
            max_retries           DSPACE_MAX_RETRIES            retries for transient failures (3)
            retry_backoff         DSPACE_RETRY_BACKOFF          base seconds, doubled per attempt (1.0)
            retry_max_wait        DSPACE_RETRY_MAX_WAIT         cap for one wait, incl. Retry-After (30)
            retry_statuses        DSPACE_RETRY_STATUSES         e.g. "429,502,503,504"
            token_refresh_margin  DSPACE_TOKEN_REFRESH_MARGIN   refresh the login token this many
                                                                seconds before it expires (60)
            reauthenticate        DSPACE_REAUTHENTICATE         log in again on 401 / expiry (true)
            accept_user_agreement DSPACE_ACCEPT_USER_AGREEMENT  DSpace-CRIS: accept the end-user
                                                                agreement for the logged-in user when
                                                                the server demands it (false)

        env_file: True (default) reads ./.env, a path reads that file, False
        reads none. Variables already set in the environment always win over
        the file, so a stray .env cannot override CI or deployment settings.
        """
        if env_file:
            # find_dotenv(usecwd=True): search from the working directory, not from site-packages
            path = find_dotenv(usecwd=True) if env_file is True else os.fspath(env_file)
            load_dotenv(dotenv_path=path or None, override=False)

        self.dspace_url = dspace_url or os.getenv("DSPACE_URL", "http://localhost:8080/server/api")
        self.version = version

        # Default for read requests: ask for metadata in all languages?
        # None -> DSPACE_ALL_LANGUAGES from the environment / .env (default: off)
        if all_languages is None:
            all_languages = _env_bool("DSPACE_ALL_LANGUAGES", False)
        self.all_languages = bool(all_languages)

        def pick(value, env_value):
            """Pick the value to use: prefer the explicit value, fallback to the environment value."""
            return env_value if value is None else value

        self.timeout = pick(timeout, _env_float("DSPACE_TIMEOUT", 30))
        self.max_retries = max(0, int(pick(max_retries, _env_int("DSPACE_MAX_RETRIES", 3))))
        self.retry_backoff = max(0.0, float(pick(retry_backoff, _env_float("DSPACE_RETRY_BACKOFF", 1.0))))
        self.retry_max_wait = max(0.0, float(pick(retry_max_wait, _env_float("DSPACE_RETRY_MAX_WAIT", 30))))
        self.retry_statuses = frozenset(retry_statuses) if retry_statuses is not None else \
            _env_int_set("DSPACE_RETRY_STATUSES", DEFAULT_RETRY_STATUSES)
        self.token_refresh_margin = max(0.0, float(pick(token_refresh_margin,
                                                         _env_float("DSPACE_TOKEN_REFRESH_MARGIN", 60))))
        self.reauthenticate = bool(pick(reauthenticate, _env_bool("DSPACE_REAUTHENTICATE", True)))
        self.accept_user_agreement = bool(pick(accept_user_agreement,
                                               _env_bool("DSPACE_ACCEPT_USER_AGREEMENT", False)))

        # Remove trailing slash if present
        if self.dspace_url.endswith('/'):
            self.dspace_url = self.dspace_url[:-1]

    @property
    def headers(self):
        """Default headers for API requests.

        Returns:
            Dict[str, str]: The default headers for API requests.
        """
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def use_all_languages(self, call_value=None):
        """
        Whether a read request should use projection=allLanguages.
        Precedence: explicit per-call value > languages() scope > config default.

        Parameters:
            call_value (Optional[bool]): Explicit per-call value for allLanguages. If None, the scope or config default is used.

        Returns:
            bool: True if allLanguages should be used for the request, False otherwise.
        """
        if call_value is not None:
            return bool(call_value)
        scoped = _all_languages_scope.get()
        return self.all_languages if scoped is None else scoped

    @contextmanager
    def languages(self, all_languages=True):
        """
        Temporarily switch allLanguages on/off for every call in the block:

            with client.config.languages(all_languages=True):
                item = client.items.get_item(uuid)

        The scope applies to all clients in the current thread/task;
        an explicit all_languages=... argument on a call still wins.

        Parameters:
            all_languages (bool): Whether to enable allLanguages for the scope of the block.
        """
        token = _all_languages_scope.set(bool(all_languages))
        try:
            yield self
        finally:
            _all_languages_scope.reset(token)


# API Endpoints
class Endpoints:
    """API endpoint constants"""

    # Authentication
    AUTHN_STATUS = "/authn/status"
    AUTHN_LOGIN = "/authn/login"
    AUTHN_LOGOUT = "/authn/logout"
    CSRF = "/security/csrf" # only DSpace 8+

    # Core API
    ITEMS = "/core/items"
    COMMUNITIES = "/core/communities"
    COLLECTIONS = "/core/collections"
    BUNDLES = "/core/bundles"
    BITSTREAMS = "/core/bitstreams"
    BITSTREAM_FORMATS = "/core/bitstreamformats"
    SITES = "/core/sites"
    ENTITY_TYPES = "/core/entitytypes"

    # Authorization / access control
    RESOURCE_POLICIES = "/authz/resourcepolicies"
    GROUPS = "/eperson/groups"
    EPERSONS = "/eperson/epersons"
    REGISTRATIONS = "/eperson/registrations"
    BULK_ACCESS_CONDITION_OPTIONS = "/config/bulkaccessconditionoptions"
    SCRIPTS = "/system/scripts"
    PROCESSES = "/system/processes"

    # Submission
    WORKSPACE_ITEMS = "/submission/workspaceitems"
    WORKFLOW_ITEMS = "/workflow/workflowitems"

    # Discovery
    DISCOVER_SEARCH = "/discover/search/objects"

    # Vocabulary
    VOCABULARIES = "/submission/vocabularies"
    VOCABULARY_ENTRIES = "/submission/vocabularyEntryDetails"


# Entity Types
class EntityTypes:
    """DSpace entity type constants"""
    PUBLICATION = "Publication"
    PERSON = "Person"
    PROJECT = "Project"
    ORGUNIT = "OrgUnit"
    JOURNAL = "Journal"
    JOURNALVOLUME = "JournalVolume"
    JOURNALISSUE = "JournalIssue"


# COAR Types
class CoarTypes:
    """Common COAR publication type constants"""
    STILL_IMAGE = "publication-coar-types:c_ecc8"
    ARTICLE = "publication-coar-types:c_6501"
    BOOK = "publication-coar-types:c_2f33"
    THESIS = "publication-coar-types:c_46ec"


# Common constants
DEFAULT_COLLECTION_UUID = "eead452e-0238-4e7e-912f-70cecd4a1f67"
