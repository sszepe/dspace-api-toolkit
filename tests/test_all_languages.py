"""
Tests for the configurable projection=allLanguages support
"""

import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import responses

import dspace_api
from dspace_api._base import BaseManager
from dspace_api.bundles import BundleManager
from dspace_api.collections import CollectionManager
from dspace_api.communities import CommunityManager
from dspace_api.config import ALL_LANGUAGES_PROJECTION, DSpaceConfig, _env_bool, with_all_languages
from dspace_api.discovery import DiscoveryManager
from dspace_api.entity_types import EntityTypeManager
from dspace_api.epersons import EPersonManager
from dspace_api.groups import GroupManager
from dspace_api.items import ItemManager
from dspace_api.site import SiteManager
from dspace_api.workspaces import WorkspaceManager

UUID = "11111111-1111-1111-1111-111111111111"
PROJ = ("projection", ALL_LANGUAGES_PROJECTION)
ANY_URL = re.compile(r"http://localhost:8080/server/api/.*")


def has_projection(request):
    return f"projection={ALL_LANGUAGES_PROJECTION}" in request.url


# ---------------------------------------------------------------------------
# Config: default, environment, precedence, scope
# ---------------------------------------------------------------------------

class TestConfig:
    @pytest.mark.parametrize("value,expected", [
        ("true", True), ("1", True), ("YES", True), (" on ", True),
        ("false", False), ("0", False), ("no", False), ("", False),
    ])
    def test_env_bool(self, monkeypatch, value, expected):
        monkeypatch.setenv("X_FLAG", value)
        assert _env_bool("X_FLAG") is expected

    def test_env_bool_missing_uses_default(self, monkeypatch):
        monkeypatch.delenv("X_FLAG", raising=False)
        assert _env_bool("X_FLAG") is False
        assert _env_bool("X_FLAG", True) is True

    def test_default_from_environment_and_explicit_wins(self, monkeypatch):
        monkeypatch.setenv("DSPACE_ALL_LANGUAGES", "true")
        assert DSpaceConfig("http://x/api").all_languages is True
        assert DSpaceConfig("http://x/api", all_languages=False).all_languages is False
        monkeypatch.delenv("DSPACE_ALL_LANGUAGES")
        assert DSpaceConfig("http://x/api").all_languages is False

    def test_precedence_call_over_scope_over_default(self):
        cfg = DSpaceConfig("http://x/api", all_languages=False)
        assert cfg.use_all_languages() is False
        assert cfg.use_all_languages(True) is True
        with cfg.languages() as same:
            assert same is cfg
            assert cfg.use_all_languages() is True
            assert cfg.use_all_languages(False) is False
            with cfg.languages(all_languages=False):
                assert cfg.use_all_languages() is False
            assert cfg.use_all_languages() is True
        assert cfg.use_all_languages() is False
        cfg.all_languages = True
        assert cfg.use_all_languages() is True

    def test_scope_is_reset_after_an_exception(self):
        cfg = DSpaceConfig("http://x/api", all_languages=False)
        with pytest.raises(RuntimeError):
            with cfg.languages():
                raise RuntimeError("boom")
        assert cfg.use_all_languages() is False


class TestWithAllLanguages:
    def test_disabled_returns_params_unchanged(self):
        params = {"a": 1}
        assert with_all_languages(params, False) is params
        assert with_all_languages(None, False) is None

    def test_none_and_list(self):
        assert with_all_languages(None, True) == [PROJ]
        assert with_all_languages([("embed", "object")], True) == [("embed", "object"), PROJ]
        assert with_all_languages([PROJ], True) == [PROJ]       # no duplicate

    def test_dict_variants(self):
        assert with_all_languages({"page": 0}, True) == {"page": 0, "projection": ALL_LANGUAGES_PROJECTION}
        assert with_all_languages({"projection": "full"}, True) == {"projection": ["full", ALL_LANGUAGES_PROJECTION]}
        assert with_all_languages({"projection": ALL_LANGUAGES_PROJECTION}, True) == \
            {"projection": ALL_LANGUAGES_PROJECTION}
        assert with_all_languages({"projection": ("a", ALL_LANGUAGES_PROJECTION)}, True) == \
            {"projection": ["a", ALL_LANGUAGES_PROJECTION]}

    def test_input_dict_not_mutated(self):
        params = {"page": 0}
        with_all_languages(params, True)
        assert params == {"page": 0}


# ---------------------------------------------------------------------------
# BaseManager-based managers (real session + responses)
# ---------------------------------------------------------------------------

GENERIC = {"uuid": UUID, "authenticated": False, "page": {"totalPages": 1}, "_embedded": {}}

READ_CALLS = [
    (CommunityManager, lambda m, **k: m.get_community("c", **k)),
    (CommunityManager, lambda m, **k: m.get_top_communities(**k)),
    (CommunityManager, lambda m, **k: m.get_subcommunities("c", **k)),
    (CommunityManager, lambda m, **k: m.get_community_collections("c", **k)),
    (CommunityManager, lambda m, **k: m.search_editable_communities("x", **k)),
    (CollectionManager, lambda m, **k: m.search_editable_collections("x", **k)),
    (CollectionManager, lambda m, **k: m.find_submit_authorized(**k)),
    (EPersonManager, lambda m, **k: m.get_eperson(UUID, **k)),
    (EPersonManager, lambda m, **k: list(m.iter_epersons(**k))),
    (EPersonManager, lambda m, **k: m.find_by_email("a@b.org", **k)),
    (EPersonManager, lambda m, **k: m.search_epersons("x", **k)),
    (EPersonManager, lambda m, **k: m.get_auth_status(**k)),
    (EPersonManager, lambda m, **k: m.get_current_user(**k)),
    (GroupManager, lambda m, **k: m.get_group(UUID, **k)),
    (GroupManager, lambda m, **k: list(m.iter_groups(**k))),
    (GroupManager, lambda m, **k: m.search_groups("x", **k)),
    (GroupManager, lambda m, **k: m.get_subgroups(UUID, **k)),
    (GroupManager, lambda m, **k: m.get_group_epersons(UUID, **k)),
    (BundleManager, lambda m, **k: m.get_item_bundles("i", **k)),
    (BundleManager, lambda m, **k: m.get_bundle("b", **k)),
    (BundleManager, lambda m, **k: m.get_bundle_bitstreams("b", **k)),
    (BundleManager, lambda m, **k: m.get_bitstream("s", **k)),
    (EntityTypeManager, lambda m, **k: m.get_submit_collections("Person", **k)),
    (BaseManager, lambda m, **k: m.get_by_href("/api/core/items/x", **k)),
]


@pytest.fixture
def any_get(http):
    http.add(responses.GET, ANY_URL, json=GENERIC)
    return http


class TestBaseManagerReads:
    @pytest.mark.parametrize("cls,call", READ_CALLS)
    def test_per_call_true(self, http_auth, any_get, cls, call):
        call(cls(http_auth), all_languages=True)
        assert any_get.calls and all(has_projection(c.request) for c in any_get.calls)

    @pytest.mark.parametrize("cls,call", READ_CALLS)
    def test_off_by_default(self, http_auth, any_get, cls, call):
        call(cls(http_auth))
        assert not any(has_projection(c.request) for c in any_get.calls)

    @pytest.mark.parametrize("cls,call", READ_CALLS)
    def test_client_default_and_per_call_false(self, http_auth, any_get, cls, call):
        http_auth.config.all_languages = True
        call(cls(http_auth))
        assert all(has_projection(c.request) for c in any_get.calls)
        any_get.calls.reset()
        call(cls(http_auth), all_languages=False)
        assert not any(has_projection(c.request) for c in any_get.calls)

    def test_scope_applies_to_methods_without_the_argument(self, http_auth, any_get):
        groups = GroupManager(http_auth)
        with http_auth.config.languages():
            groups.search_groups_not_member_of(UUID)        # no all_languages parameter
        assert has_projection(any_get.calls[-1].request)

    def test_projection_combined_with_embed(self, http_auth, any_get):
        GroupManager(http_auth).get_group(UUID, embed_object=True, all_languages=True)
        assert any_get.calls[-1].request.params == {"embed": "object", "projection": ALL_LANGUAGES_PROJECTION}

    def test_writes_never_get_the_projection(self, http_auth, http, api_url):
        http_auth.config.all_languages = True
        http.add(responses.POST, api_url("/core/items/i/bundles"), status=201, json={"uuid": "b"})
        BundleManager(http_auth).create_bundle("i", "EXTRA")
        assert not has_projection(http.calls[-1].request)

    def test_binary_download_never_gets_the_projection(self, http_auth, http, api_url):
        http_auth.config.all_languages = True
        http.add(responses.GET, api_url("/core/bitstreams/s/content"), body=b"data")
        assert BundleManager(http_auth).download_bitstream("s") == b"data"
        assert not has_projection(http.calls[-1].request)

    def test_config_without_the_feature(self, dspace_config, http):
        """Config objects without use_all_languages (old toolkit / test doubles) still work"""
        cfg = SimpleNamespace(dspace_url=dspace_config.dspace_url, headers={}, timeout=5)
        auth = Mock(spec=["session", "config"])
        auth.session, auth.config = requests.Session(), cfg
        http.add(responses.GET, ANY_URL, json={})
        mgr = BaseManager(auth)
        mgr._get_json(mgr._url("/x"), "op")
        assert not has_projection(http.calls[-1].request)
        mgr._get_json(mgr._url("/x"), "op", all_languages=True)
        assert has_projection(http.calls[-1].request)


class TestSite:
    def test_site_defaults_to_all_languages(self, http_auth, any_get):
        site = SiteManager(http_auth)
        site.get_site()
        assert has_projection(any_get.calls[-1].request)
        site.get_site(all_languages=None)                   # follow the client setting (off)
        assert not has_projection(any_get.calls[-1].request)
        site.get_site(all_languages=False)
        assert not has_projection(any_get.calls[-1].request)


class TestPatchDecidingReadsUseAllLanguages:
    """Reads that decide add/replace or a PATCH index must see every language"""

    def test_eperson_upsert_and_agreement(self, http_auth, http, api_url):
        person = {"uuid": UUID, "metadata": {}}
        http.add(responses.GET, api_url(f"/eperson/epersons/{UUID}"), json=person)
        http.add(responses.PATCH, api_url(f"/eperson/epersons/{UUID}"), json=person)
        http.add(responses.GET, api_url("/authn/status"), json={"authenticated": True})
        epersons = EPersonManager(http_auth)
        epersons.upsert_eperson_metadata(UUID, {"eperson.phone": "1"})
        epersons.accept_end_user_agreement(UUID)
        epersons.revoke_end_user_agreement(UUID)
        gets = [c.request for c in http.calls if c.request.method == "GET" and "/epersons/" in c.request.url]
        assert len(gets) == 3 and all(has_projection(r) for r in gets)


# ---------------------------------------------------------------------------
# Older managers (direct session.get with dict params)
# ---------------------------------------------------------------------------

def ok(payload=None):
    return Mock(status_code=200, json=Mock(return_value=payload or {}))


OLD_CALLS = [
    (ItemManager, lambda m, **k: m.get_items(**k), {"page": 0, "size": 20}),
    (ItemManager, lambda m, **k: m.get_item("i", **k), None),
    (CollectionManager, lambda m, **k: m.get_collections(**k), {"page": 0, "size": 20}),
    (CollectionManager, lambda m, **k: m.get_collection("c", **k), None),
    (WorkspaceManager, lambda m, **k: m.get_workspace_item(1, **k), None),
    (WorkspaceManager, lambda m, **k: m.get_workspace_items(**k), {"page": 0, "size": 20}),
]


class TestOlderManagers:
    @pytest.mark.parametrize("cls,call,base", OLD_CALLS)
    def test_off_on_and_client_default(self, mock_auth, cls, call, base):
        mgr = cls(mock_auth)
        mgr.session.get = Mock(return_value=ok())

        call(mgr)
        assert mgr.session.get.call_args.kwargs["params"] == base

        call(mgr, all_languages=True)
        params = mgr.session.get.call_args.kwargs["params"]
        assert params == ([PROJ] if base is None else {**base, "projection": ALL_LANGUAGES_PROJECTION})

        mock_auth.config.all_languages = True
        call(mgr)
        assert mgr.session.get.call_args.kwargs["params"] == params
        call(mgr, all_languages=False)
        assert mgr.session.get.call_args.kwargs["params"] == base

    def test_discovery_combines_with_existing_projection(self, mock_auth):
        mgr = DiscoveryManager(mock_auth)
        mgr.session.get = Mock(return_value=ok())
        mgr.search_objects("q")
        assert mgr.session.get.call_args.kwargs["params"]["projection"] == "preventMetadataSecurity"
        mgr.search_objects("q", all_languages=True)
        assert mgr.session.get.call_args.kwargs["params"]["projection"] == \
            ["preventMetadataSecurity", ALL_LANGUAGES_PROJECTION]
        with mock_auth.config.languages():
            mgr.find_by_title("x")                          # helpers follow the scope
        assert ALL_LANGUAGES_PROJECTION in mgr.session.get.call_args.kwargs["params"]["projection"]

    def test_discovery_get_all_results_passes_flag(self, mock_auth):
        mgr = DiscoveryManager(mock_auth)
        mgr.session.get = Mock(return_value=ok({"_embedded": {"searchResult": {
            "_embedded": {"objects": []}, "page": {"number": 0, "totalPages": 1}}}}))
        mgr.get_all_results("*:*", all_languages=True)
        assert ALL_LANGUAGES_PROJECTION in mgr.session.get.call_args.kwargs["params"]["projection"]

    @pytest.mark.parametrize("method,payload", [
        ("upsert_metadata", {"dc.title": "T"}),
        ("upsert_metadata_by_position", {"dc.title": {"value": "T", "place": 0}}),
    ])
    def test_item_upserts_always_read_all_languages(self, mock_auth, method, payload):
        mgr = ItemManager(mock_auth)                        # config default is off
        mgr.session.get = Mock(return_value=ok({"uuid": "i", "metadata": {}}))
        mgr.session.patch = Mock(return_value=ok({"uuid": "i"}))
        getattr(mgr, method)("i", payload)
        assert mgr.session.get.call_args.kwargs["params"] == [PROJ]


class TestClient:
    def test_client_passes_all_languages_to_config(self, monkeypatch):
        class FakeAuth:
            def __init__(self, config):
                self.config = config
                self.session = Mock()

        monkeypatch.setattr(dspace_api, "DSpaceAuth", FakeAuth, raising=True)
        client = dspace_api.DSpaceClient(dspace_url="http://localhost:8080/server/api", all_languages=True)
        assert client.config.all_languages is True
        assert client.items.config.use_all_languages() is True
        client = dspace_api.DSpaceClient(dspace_url="http://localhost:8080/server/api", all_languages=False)
        assert client.config.all_languages is False
