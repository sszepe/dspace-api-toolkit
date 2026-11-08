"""
Tests for authorizations.py (permission checks via /authz/authorizations and /authz/features)
"""

import pytest
import responses

from dspace_api.authorizations import AuthorizationManager, Feature, feature_of, parse_authorization_id
from dspace_api.exceptions import AuthorizationError

API = "http://localhost:8080/server/api"
ITEM = "11111111-1111-1111-1111-111111111111"
ITEM2 = "22222222-2222-2222-2222-222222222222"
COLL = "cccccccc-cccc-cccc-cccc-cccccccccccc"
EP = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
SITE = "5555555-5555-5555-5555-555555555555"
ITEM_URL = f"{API}/core/items/{ITEM}"


@pytest.fixture
def mgr(http_auth):
    return AuthorizationManager(http_auth)


def last(http):
    return http.calls[-1].request


def params(http):
    from urllib.parse import parse_qsl, urlparse
    return parse_qsl(urlparse(last(http).url).query)


def authz(feature, object_id=ITEM, kind="core.item", eperson=EP, embed=True):
    ident = "_".join(p for p in (eperson, feature, kind, object_id) if p)
    result = {"id": ident, "type": "authorization"}
    if embed:
        result["_embedded"] = {"feature": {"id": feature}}
    return result


def page(*authorizations):
    return {"_embedded": {"authorizations": list(authorizations)}, "page": {"totalPages": 1}}


class TestParsing:
    def test_parse_with_and_without_eperson(self):
        assert parse_authorization_id(f"{EP}_canEditMetadata_core.item_{ITEM}") == {
            "eperson": EP, "feature": "canEditMetadata", "object_type": "core.item", "object_id": ITEM}
        assert parse_authorization_id("canSubmit_submission.workspaceitem_42") == {
            "eperson": None, "feature": "canSubmit", "object_type": "submission.workspaceitem", "object_id": "42"}
        assert parse_authorization_id("f_t_a_b")["object_id"] == "a_b"
        with pytest.raises(ValueError):
            parse_authorization_id("nonsense")

    def test_feature_of(self):
        assert feature_of(authz("withdrawItem")) == "withdrawItem"
        assert feature_of(authz("canDelete", embed=False)) == "canDelete"
        assert feature_of({"id": "bad"}) is None
        assert feature_of({}) is None


class TestObjectUri:
    def test_variants(self, mgr):
        assert mgr.object_uri(("item", ITEM)) == ITEM_URL
        assert mgr.object_uri(("workspaceitem", 42)) == f"{API}/submission/workspaceitems/42"
        assert mgr.object_uri({"uuid": ITEM, "type": "item"}) == ITEM_URL
        assert mgr.object_uri({"id": 7, "type": "version"}) == f"{API}/versioning/versions/7"
        assert mgr.object_uri({"_links": {"self": {"href": ITEM_URL}}}) == ITEM_URL
        assert mgr.object_uri(f"/api/core/items/{ITEM}") == ITEM_URL
        assert mgr.object_uri(ITEM_URL) == ITEM_URL

    @pytest.mark.parametrize("bad", [("thing", 1), {"uuid": ITEM}, {"type": "item"}, ITEM, 5])
    def test_invalid(self, mgr, bad):
        with pytest.raises(ValueError):
            mgr.object_uri(bad)

    def test_foreign_host(self, mgr):
        with pytest.raises(ValueError):
            mgr.object_uri("https://evil.example.org/api/core/items/x")


class TestChecks:
    def test_search_object(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authz/authorizations/search/object"), json=page(authz("canDelete")))
        assert len(mgr.search_object(("item", ITEM), feature="canDelete", eperson={"uuid": EP}, embed="feature")) == 1
        p = dict(params(http))
        assert p["uri"] == ITEM_URL and p["feature"] == "canDelete" and p["eperson"] == EP
        assert p["embed"] == "feature"
        mgr.search_object(("item", ITEM))
        assert set(dict(params(http))) == {"uri", "page", "size"}

    def test_eperson_must_be_uuid(self, mgr):
        with pytest.raises(ValueError):
            mgr.search_object(("item", ITEM), eperson="jane@example.org")

    def test_can_and_require(self, mgr, http, api_url):
        url = api_url("/authz/authorizations/search/object")
        http.add(responses.GET, url, json=page(authz("canMove")))
        assert mgr.can(("item", ITEM), Feature.CAN_MOVE) is True
        mgr.require(("item", ITEM), Feature.CAN_MOVE)
        http.replace(responses.GET, url, json=page())
        assert mgr.can(("item", ITEM), Feature.CAN_MOVE) is False
        with pytest.raises(AuthorizationError, match="the current user lacks 'canMove'"):
            mgr.require(("item", ITEM), Feature.CAN_MOVE)
        with pytest.raises(AuthorizationError, match=f"eperson {EP}") as info:
            mgr.require(("item", ITEM), Feature.CAN_MOVE, eperson=EP)
        assert info.value.status_code == 403

    def test_features_on_and_check(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authz/authorizations/search/object"),
                 json=page(authz("canEditMetadata"), authz("withdrawItem", embed=False), {"id": "garbage"}))
        assert mgr.features_on(("item", ITEM)) == {"canEditMetadata", "withdrawItem"}
        assert dict(params(http))["embed"] == "feature"
        assert mgr.check(("item", ITEM), ["withdrawItem", "canDelete"]) == {"withdrawItem": True, "canDelete": False}

    def test_get_authorization(self, mgr, http, api_url):
        ident = f"{EP}_canDelete_core.item_{ITEM}"
        http.add(responses.GET, api_url(f"/authz/authorizations/{ident}"), json={"id": ident})
        assert mgr.get_authorization(ident, embed="feature")["id"] == ident


class TestBatch:
    def test_features_on_many(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authz/authorizations/search/objects"), json=page(
            authz("canEditMetadata", ITEM), authz("canDelete", ITEM2), authz("canEditMetadata", "unknown"),
            {"id": "garbage"}))
        result = mgr.features_on_many([("item", ITEM), {"uuid": ITEM2, "type": "item"}],
                                      ["canEditMetadata", "canDelete"], eperson=EP)
        assert result == {ITEM: {"canEditMetadata"}, ITEM2: {"canDelete"}}
        p = params(http)
        assert ("type", "core.items") in p and ("uuid", ITEM) in p and ("uuid", ITEM2) in p
        assert ("eperson", EP) in p and ("feature", "canDelete") in p

    def test_all_features_checks_each_object(self, mgr, http, api_url):
        # search/objects without feature= fails server-side (NPE -> 500) -> search/object per object
        http.add(responses.GET, api_url("/authz/authorizations/search/object"),
                 json=page(authz("canEditMetadata"), authz("canDelete")))
        assert mgr.features_on_many([("item", ITEM), ("item", ITEM2)]) == {
            ITEM: {"canDelete", "canEditMetadata"}, ITEM2: {"canDelete", "canEditMetadata"}}
        assert not [c for c in http.calls if "search/objects" in c.request.url]

    def test_feature_filter_and_batches(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authz/authorizations/search/objects"),
                 json=page(authz("canEditMetadata", ITEM), authz("canDelete", ITEM)))
        result = mgr.features_on_many([("item", ITEM), ("item", ITEM2), ("collection", COLL)],
                                      ["canEditMetadata"], batch_size=1)
        assert result == {ITEM: {"canEditMetadata"}, ITEM2: set(), COLL: set()}
        object_calls = [c for c in http.calls if "search/objects" in c.request.url]
        assert len(object_calls) == 3
        assert "type=core.collections" in object_calls[-1].request.url
        assert "feature=canEditMetadata" in object_calls[0].request.url

    def test_can_many(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authz/authorizations/search/objects"),
                 json=page(authz("canEditMetadata", ITEM)))
        assert mgr.can_many([("item", ITEM), ("item", ITEM2)], "canEditMetadata") == {ITEM: True, ITEM2: False}

    def test_fallback_for_old_servers(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authz/authorizations/search/objects"), status=404)
        http.add(responses.GET, api_url("/authz/authorizations/search/object"),
                 json=page(authz("canEditMetadata"), authz("canDelete")))
        assert mgr.features_on_many([("item", ITEM)], "canDelete") == {ITEM: {"canDelete"}}
        http.replace(responses.GET, api_url("/authz/authorizations/search/objects"), status=500)
        assert mgr.features_on_many([("item", ITEM)], "canDelete") == {ITEM: {"canDelete"}}

    def test_other_errors_propagate(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authz/authorizations/search/objects"), status=403)
        with pytest.raises(AuthorizationError):
            mgr.features_on_many([("item", ITEM)], "canDelete")


class TestConvenience:
    @pytest.fixture
    def site(self, http, api_url):
        http.add(responses.GET, api_url("/core/sites"), json={"_embedded": {"sites": [{"uuid": SITE}]}})
        return f"{API}/core/sites/{SITE}"

    def test_site_admin(self, mgr, http, api_url, site):
        http.add(responses.GET, api_url("/authz/authorizations/search/object"),
                 json=page(authz("administratorOf", SITE, "core.site")))
        assert mgr.site_uri() == site
        assert mgr.is_site_admin() is True
        assert ("uri", site) in params(http) and ("feature", "administratorOf") in params(http)

    def test_admin_scope(self, mgr, http, api_url, site):
        http.add(responses.GET, api_url("/authz/authorizations/search/object"),
                 json=page(authz("isCollectionAdmin", SITE, "core.site")))
        assert mgr.admin_scope() == {"site": False, "any_community": False, "any_collection": True}

    def test_no_site(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/sites"), json={"_embedded": {"sites": []}})
        with pytest.raises(ValueError):
            mgr.site_uri()

    def test_is_admin_of(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authz/authorizations/search/object"), json=page())
        assert mgr.is_admin_of(("collection", COLL)) is False


class TestFeatures:
    def test_features(self, mgr, http, api_url):
        features = {"_embedded": {"features": [{"id": "canDelete"}]}, "page": {"totalPages": 1}}
        http.add(responses.GET, api_url("/authz/features"), json=features)
        http.add(responses.GET, api_url("/authz/features/canDelete"), json={"id": "canDelete"})
        http.add(responses.GET, api_url("/authz/features/search/resourcetype"), json=features)
        assert mgr.list_features() == [{"id": "canDelete"}]
        assert mgr.get_feature("canDelete")["id"] == "canDelete"
        assert mgr.features_for_type("core.item") == [{"id": "canDelete"}]
        assert ("type", "core.item") in params(http)
