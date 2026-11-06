import dspace_api
from dspace_api.config import DSpaceConfig


class TestClientAndConfig:
    """Class-based tests for client and config."""

    def test_dspace_client_init_with_credentials_and_close(self, monkeypatch):
        """Test DSpaceClient initialization with credentials and close() method"""
        # Fake auth object that mimics the shape DSpaceClient expects
        class FakeAuth:
            def __init__(self, config):
                self.config = config
                self.session = __import__("unittest").mock.Mock()
                self.logged_in = False
                self.logged_out = False

            def login(self, username, password):
                self.logged_in = True

            def logout(self):
                self.logged_out = True

        # Monkeypatch the DSpaceAuth symbol used inside dspace_api.__init__
        monkeypatch.setattr(dspace_api, "DSpaceAuth", FakeAuth, raising=True)

        # Build client with credentials -> should call login and init managers
        client = dspace_api.DSpaceClient(
            dspace_url="http://localhost:8080/server/api",
            username="u",
            password="p",
            version=8,
        )

        # Auto-login happened
        assert isinstance(client.auth, FakeAuth)
        assert client.auth.logged_in is True

        # Managers were constructed with the same auth/session/config
        for manager_name in (
            "items",
            "workspace",
            "bundles",
            "collections",
            "discovery",
            "vocabulary",
        ):
            manager = getattr(client, manager_name)
            assert manager.auth is client.auth
            assert manager.session is client.auth.session
            assert manager.config is client.auth.config

        # close() calls logout()
        client.close()
        assert client.auth.logged_out is True

    def test_config_trailing_slash_removed(self):
        """Test DSpaceConfig removes trailing slash from dspace_url"""
        cfg = DSpaceConfig("http://localhost:8080/server/api/")
        # Must strip the final slash
        assert cfg.dspace_url == "http://localhost:8080/server/api"


    def test_dspace_client_without_credentials_does_not_login(self, monkeypatch):
        """Test DSpaceClient only logs in when both username and password are given"""
        calls = []

        class FakeAuth:
            def __init__(self, config):
                self.config = config
                self.session = __import__("unittest").mock.Mock()

            def login(self, username, password):
                calls.append((username, password))

        monkeypatch.setattr(dspace_api, "DSpaceAuth", FakeAuth, raising=True)
        dspace_api.DSpaceClient(dspace_url="http://localhost:8080/server/api")
        dspace_api.DSpaceClient(dspace_url="http://localhost:8080/server/api", username="u")
        assert calls == []

    def test_dspace_client_exposes_permission_manager(self, monkeypatch):
        """Test DSpaceClient wires up PermissionManager as client.permissions"""
        from dspace_api.permissions import PermissionManager

        class FakeAuth:
            def __init__(self, config):
                self.config = config
                self.session = __import__("unittest").mock.Mock()

        monkeypatch.setattr(dspace_api, "DSpaceAuth", FakeAuth, raising=True)
        client = dspace_api.DSpaceClient(dspace_url="http://localhost:8080/server/api")
        assert isinstance(client.permissions, PermissionManager)
        assert client.permissions.auth is client.auth

    def test_dspace_client_exposes_access_managers(self, monkeypatch):
        """Test DSpaceClient wires up the community/group/eperson/site/entity-type managers"""
        from dspace_api.communities import CommunityManager
        from dspace_api.entity_types import EntityTypeManager
        from dspace_api.epersons import EPersonManager
        from dspace_api.groups import GroupManager
        from dspace_api.site import SiteManager

        class FakeAuth:
            def __init__(self, config):
                self.config = config
                self.session = __import__("unittest").mock.Mock()

        monkeypatch.setattr(dspace_api, "DSpaceAuth", FakeAuth, raising=True)
        client = dspace_api.DSpaceClient(dspace_url="http://localhost:8080/server/api")
        for attr, cls in (("communities", CommunityManager), ("groups", GroupManager),
                          ("epersons", EPersonManager), ("site", SiteManager),
                          ("entity_types", EntityTypeManager)):
            manager = getattr(client, attr)
            assert isinstance(manager, cls)
            assert manager.session is client.auth.session
