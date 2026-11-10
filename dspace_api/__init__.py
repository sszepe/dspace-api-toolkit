# -*- coding: UTF-8 -*-
"""DSpace API toolkit"""
from ._version import __version__
from .config import DSpaceConfig
from .auth import DSpaceAuth
from .items import ItemManager
from .workspaces import WorkspaceManager
from .bundles import BundleManager
from .collections import CollectionManager
from .discovery import DiscoveryManager
from .vocabulary import VocabularyManager
from .permissions import PermissionManager
from .communities import CommunityManager
from .groups import GroupManager
from .epersons import EPersonManager
from .site import SiteManager
from .entity_types import EntityTypeManager
from .metadata import MetadataManager, MetadataPatch
from .authorizations import AuthorizationManager, Feature
from .versions import VersionManager
from .relationships import RelationshipManager
from .workflow import WorkflowError, WorkflowManager
from .workspaces import SubmissionError
from .exceptions import OnBehalfOfError, UserAgreementRequired
from . import on_behalf as _on_behalf


class DSpaceClient:
    """Client for interacting with the DSpace REST API."""
    def __init__(self, dspace_url=None, username=None, password=None, version=8, all_languages=None):
        """Initialize the DSpace client with optional URL, credentials, API version, and language settings.
        
        Parameters:
            dspace_url: The base URL of the DSpace instance.
            username: The username for authentication.
            password: The password for authentication.
            version: The API version to use (default is 8).
            all_languages: Optional flag for handling all languages in metadata.

        """
        # all_languages: default for projection=allLanguages on reads
        # (None -> DSPACE_ALL_LANGUAGES env var, default off)
        self.config = DSpaceConfig(dspace_url, version, all_languages=all_languages)
        # Initialize authentication manager with the given configuration.
        self.auth = DSpaceAuth(self.config)
        # Log in if username and password are provided.
        if username and password:
            self.auth.login(username, password)
        # Initialize managers for various DSpace API components.
        self.items = ItemManager(self.auth)
        self.workspace = WorkspaceManager(self.auth)
        self.bundles = BundleManager(self.auth)
        self.collections = CollectionManager(self.auth)
        self.discovery = DiscoveryManager(self.auth)
        self.vocabulary = VocabularyManager(self.auth)
        self.permissions = PermissionManager(self.auth)
        self.communities = CommunityManager(self.auth)
        self.groups = GroupManager(self.auth)
        self.epersons = EPersonManager(self.auth)
        self.site = SiteManager(self.auth)
        self.entity_types = EntityTypeManager(self.auth)
        self.metadata = MetadataManager(self.auth)
        self.authorizations = AuthorizationManager(self.auth)
        self.versions = VersionManager(self.auth)
        self.relationships = RelationshipManager(self.auth)
        self.workflow = WorkflowManager(self.auth)

    def on_behalf_of(self, eperson, verify=True):
        """
        Context manager: requests in the block are made as `eperson` (UUID,
        email or eperson dict) via X-On-Behalf-Of ("login as"). Needs an
        administrator login and webui.user.assumelogin = true on the server;
        verify=True checks that once and raises OnBehalfOfError with the
        reason. See dspace_api/on_behalf.py.

            with client.on_behalf_of("jane@example.org"):
                client.items.get_item(uuid)
        """
        return _on_behalf.on_behalf_of(self.auth, eperson, verify=verify)

    def close(self):
        """Close the client and log out from the DSpace API.

        Parameters:
            None

        Returns:
            None
        """
        self.auth.logout()
