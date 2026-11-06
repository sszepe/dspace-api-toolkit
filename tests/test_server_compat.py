"""
Server differences handled in the session layer (session.py / auth.py):

* optional projections (allLanguages, preventMetadataSecurity, ...) that
  DSpace 8/9 do not know -> checked once, then left out
* multi-value metadata PATCH -> remove + one append per value
* DSpace-CRIS end-user agreement (403 "No user agreement accepted")
"""

import json
from urllib.parse import parse_qsl, urlsplit
from unittest.mock import Mock

import pytest
import requests
import responses

from dspace_api.auth import DSpaceAuth
from dspace_api.config import DSpaceConfig
from dspace_api.exceptions import AuthorizationError, UserAgreementRequired, handle_response_error
from dspace_api.items import ItemManager
from dspace_api.session import (DSpaceSession, expand_metadata_operations, is_user_agreement_response,
                                requested_projections, strip_projections)

API = "http://localhost:8080/server/api"
ITEM = API + "/core/items/i1"
SITES = API + "/core/sites"
PERSON = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AGREEMENT_403 = "No user agreement accepted"


def make_auth(**settings):
    auth = DSpaceAuth(DSpaceConfig(API, all_languages=False, max_retries=0, env_file=False, **settings))
    auth.session.sleep = Mock()
    auth._authenticated = True
    return auth


@pytest.fixture
def auth():
    return make_auth()


def projections_of(request):
    return [v for k, v in parse_qsl(urlsplit(request.url).query) if k == "projection"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestProjectionHelpers:
    def test_requested_projections(self):
        assert requested_projections(f"{ITEM}?projection=full,allLanguages&x=1", None) == ["full", "allLanguages"]
        assert requested_projections(ITEM, {"projection": ["allLanguages", "full"]}) == ["allLanguages", "full"]
        assert requested_projections(ITEM, {"projection": None, "a": 1}) == []
        assert requested_projections(ITEM, [("projection", "a,b"), ("embed", "x"), "junk", ("p",)]) == ["a", "b"]
        assert requested_projections(ITEM, "raw=string") == []

    def test_strip_from_url(self):
        url, params = strip_projections(f"{ITEM}?projection=allLanguages&embed=x", None, {"allLanguages"})
        assert url == f"{ITEM}?embed=x" and params is None
        url, _ = strip_projections(f"{ITEM}?projection=full,allLanguages", None, {"allLanguages"})
        assert url == f"{ITEM}?projection=full"
        assert strip_projections(ITEM, None, {"allLanguages"}) == (ITEM, None)

    def test_strip_from_dict(self):
        assert strip_projections(ITEM, {"projection": "allLanguages", "a": 1}, {"allLanguages"})[1] == {"a": 1}
        assert strip_projections(ITEM, {"projection": ["full", "allLanguages", "x"]}, {"allLanguages"})[1] == {
            "projection": ["full", "x"]}
        assert strip_projections(ITEM, {"projection": ["full", "allLanguages"]}, {"allLanguages"})[1] == {
            "projection": "full"}
        original = {"projection": None}
        assert strip_projections(ITEM, original, {"allLanguages"})[1] == {"projection": None}

    def test_strip_from_list(self):
        params = [("projection", "allLanguages"), ("projection", "full,allLanguages"), ("embed", "x"), "odd"]
        assert strip_projections(ITEM, params, {"allLanguages"})[1] == [("projection", "full"), ("embed", "x"), "odd"]
        assert strip_projections(ITEM, (("a", 1),), {"allLanguages"})[1] == [("a", 1)]


class TestMetadataExpansion:
    def test_multi_value_add_and_replace(self):
        ops = [{"op": "add", "path": "/metadata/dc.subject", "value": [{"value": "a"}, {"value": "b"}]},
               {"op": "replace", "path": "/metadata/dc.title", "value": [{"value": "T1"}, {"value": "T2"}]}]
        assert expand_metadata_operations(ops) == [
            {"op": "add", "path": "/metadata/dc.subject/-", "value": {"value": "a"}},
            {"op": "add", "path": "/metadata/dc.subject/-", "value": {"value": "b"}},
            {"op": "remove", "path": "/metadata/dc.title"},
            {"op": "add", "path": "/metadata/dc.title/-", "value": {"value": "T1"}},
            {"op": "add", "path": "/metadata/dc.title/-", "value": {"value": "T2"}}]

    @pytest.mark.parametrize("ops", [
        [{"op": "add", "path": "/metadata/dc.subject", "value": [{"value": "a"}]}],         # single value
        [{"op": "add", "path": "/metadata/dc.subject/0", "value": [{"value": "a"}, {"value": "b"}]}],
        [{"op": "add", "path": "/sections/page/dc.subject", "value": [{"value": "a"}, {"value": "b"}]}],
        [{"op": "move", "path": "/metadata/dc.subject", "value": [1, 2]}],
        [{"op": "replace", "path": "/withdrawn", "value": True}],
        ["not an operation"],
    ])
    def test_untouched(self, ops):
        assert expand_metadata_operations(ops) is None

    def test_not_a_list(self):
        assert expand_metadata_operations({"op": "add"}) is None


# ---------------------------------------------------------------------------
# Optional projections in the request pipeline
# ---------------------------------------------------------------------------

class TestOptionalProjections:
    def test_unknown_projection_is_dropped_and_remembered(self, auth, http):
        http.add(responses.GET, ITEM, status=400)          # with projection
        http.add(responses.GET, SITES, status=400)         # probe: unknown
        http.add(responses.GET, ITEM, json={"uuid": "i1"})  # resent without it
        response = auth.session.get(ITEM, params={"projection": "allLanguages", "embed": "x"})
        assert response.json() == {"uuid": "i1"}
        assert projections_of(http.calls[1].request) == ["allLanguages"]       # the probe
        assert projections_of(http.calls[2].request) == []
        assert auth.session.projection_support == {"allLanguages": False}

        # later requests leave it out right away, without a probe
        http.replace(responses.GET, ITEM, json={"uuid": "i1"})
        auth.session.get(ITEM, params=[("projection", "allLanguages"), ("projection", "full")])
        assert projections_of(http.calls[-1].request) == ["full"]
        assert len([c for c in http.calls if c.request.url.startswith(SITES)]) == 1
        # nothing to strip
        auth.session.get(ITEM, params={"a": 1})
        assert projections_of(http.calls[-1].request) == []

    def test_supported_projection_is_kept_and_404_returned(self, auth, http):
        # CRIS / DSpace 10: the projection exists, the 404 is a real "not found"
        http.add(responses.GET, ITEM, status=404)
        http.add(responses.GET, SITES, json={"_embedded": {"sites": []}})
        assert auth.session.get(ITEM, params={"projection": "allLanguages"}).status_code == 404
        assert auth.session.projection_support == {"allLanguages": True}
        assert auth.session.get(ITEM, params={"projection": "allLanguages"}).status_code == 404
        assert len(http.calls) == 3                                     # no second probe
        assert projections_of(http.calls[-1].request) == ["allLanguages"]

    def test_other_projections_and_methods_are_not_probed(self, auth, http):
        http.add(responses.GET, ITEM, status=404)
        http.add(responses.POST, ITEM, status=400)
        assert auth.session.get(ITEM, params={"projection": "full"}).status_code == 404
        assert auth.session.post(ITEM, params={"projection": "allLanguages"}).status_code == 400
        assert auth.session.get(ITEM).status_code == 404
        assert len(http.calls) == 3

    def test_unclear_probe_keeps_projection_and_asks_again(self, auth, http):
        http.add(responses.GET, ITEM, status=400)
        http.add(responses.GET, SITES, status=500)
        assert auth.session.get(ITEM, params={"projection": "allLanguages"}).status_code == 400
        assert auth.session.projection_support == {}
        http.replace(responses.GET, SITES, body=requests.exceptions.ConnectionError("down"))
        assert auth.session.supports_projection("allLanguages") is True
        assert auth.session.projection_support == {}

    def test_supports_projection_cached_and_without_base_url(self, auth, http):
        auth.session.projection_support["allLanguages"] = False
        assert auth.session.supports_projection("allLanguages") is False
        assert DSpaceSession().supports_projection("allLanguages") is True       # no auth -> no probe
        assert len(http.calls) == 0

    def test_concurrent_probe_result_reused_inside_lock(self, auth, http):
        session = auth.session

        class Lock:
            def __enter__(self):
                session.projection_support["allLanguages"] = True   # another thread finished first
            def __exit__(self, *exc):
                return False

        session._projection_lock = Lock()
        assert session.supports_projection("allLanguages") is True
        assert len(http.calls) == 0

    def test_older_manager_with_projection_in_dict_params(self, http):
        auth = make_auth()
        auth.config.all_languages = True
        http.add(responses.GET, ITEM, status=404)
        http.add(responses.GET, SITES, status=404)
        http.add(responses.GET, ITEM, json={"uuid": "i1"})
        assert ItemManager(auth).get_item("i1")["uuid"] == "i1"


# ---------------------------------------------------------------------------
# Metadata PATCH in the request pipeline
# ---------------------------------------------------------------------------

class TestMetadataPatchPipeline:
    OPS = [{"op": "add", "path": "/metadata/dc.subject", "value": [{"value": "a"}, {"value": "b"}]}]
    EXPANDED = [{"op": "add", "path": "/metadata/dc.subject/-", "value": {"value": "a"}},
                {"op": "add", "path": "/metadata/dc.subject/-", "value": {"value": "b"}}]

    def test_json_body(self, auth, http):
        http.add(responses.PATCH, ITEM, json={})
        auth.session.patch(ITEM, json=self.OPS)
        assert json.loads(http.calls[-1].request.body) == self.EXPANDED
        auth.session.patch(ITEM, json=[{"op": "remove", "path": "/metadata/dc.subject"}])
        assert json.loads(http.calls[-1].request.body) == [{"op": "remove", "path": "/metadata/dc.subject"}]

    @pytest.mark.parametrize("encode", [str, lambda s: s.encode()])
    def test_data_body(self, auth, http, encode):
        http.add(responses.PATCH, ITEM, json={})
        auth.session.patch(ITEM, data=encode(json.dumps(self.OPS)))
        assert json.loads(http.calls[-1].request.body) == self.EXPANDED

    @pytest.mark.parametrize("data", ["[not json", '[{"op": "remove", "path": "/metadata/x"}]', "x=1", None])
    def test_other_bodies_untouched(self, auth, http, data):
        http.add(responses.PATCH, ITEM, json={})
        auth.session.patch(ITEM, data=data)
        body = http.calls[-1].request.body
        assert (body.decode() if isinstance(body, bytes) else body) == data

    def test_upsert_metadata_keeps_every_value(self, auth, http):
        http.add(responses.GET, ITEM, json={"uuid": "i1", "metadata": {"dc.title": [{"value": "old"}]}})
        http.add(responses.PATCH, ITEM, json={"uuid": "i1"})
        ItemManager(auth).upsert_metadata("i1", {"dc.subject": ["a", "b"], "dc.title": ["n1", "n2"]})
        assert json.loads(http.calls[-1].request.body) == [
            {"op": "add", "path": "/metadata/dc.subject/-", "value": {"value": "a"}},
            {"op": "add", "path": "/metadata/dc.subject/-", "value": {"value": "b"}},
            {"op": "remove", "path": "/metadata/dc.title"},
            {"op": "add", "path": "/metadata/dc.title/-", "value": {"value": "n1"}},
            {"op": "add", "path": "/metadata/dc.title/-", "value": {"value": "n2"}}]


# ---------------------------------------------------------------------------
# DSpace-CRIS end-user agreement
# ---------------------------------------------------------------------------

def status_json(metadata=None, uuid=PERSON):
    person = {"uuid": uuid, "email": "admin@example.org", "metadata": metadata or {}} if uuid else None
    return {"authenticated": True, "_embedded": {"eperson": person} if person else {}}


class TestUserAgreement:
    def test_error_class(self):
        response = Mock(status_code=403, text=AGREEMENT_403)
        with pytest.raises(UserAgreementRequired, match="end-user agreement") as info:
            handle_response_error(response, "Create community")
        assert isinstance(info.value, AuthorizationError) and info.value.status_code == 403
        with pytest.raises(AuthorizationError) as plain:
            handle_response_error(Mock(status_code=403, text=None), "x")
        assert not isinstance(plain.value, UserAgreementRequired)

    def test_detection(self):
        assert is_user_agreement_response(Mock(status_code=403, text=AGREEMENT_403))
        assert not is_user_agreement_response(Mock(status_code=403, text="Forbidden"))
        assert not is_user_agreement_response(Mock(status_code=200, text=AGREEMENT_403))
        assert not is_user_agreement_response(Mock(status_code=403, text=None))

    def test_config_flag(self, monkeypatch):
        monkeypatch.delenv("DSPACE_ACCEPT_USER_AGREEMENT", raising=False)
        assert DSpaceConfig(API, env_file=False).accept_user_agreement is False
        monkeypatch.setenv("DSPACE_ACCEPT_USER_AGREEMENT", "true")
        assert DSpaceConfig(API, env_file=False).accept_user_agreement is True
        assert DSpaceConfig(API, env_file=False, accept_user_agreement=False).accept_user_agreement is False

    def test_off_by_default_403_is_returned(self, auth, http):
        http.add(responses.POST, ITEM, status=403, body=AGREEMENT_403)
        assert auth.session.post(ITEM).status_code == 403
        assert len(http.calls) == 1

    def test_auto_accept_patches_refreshes_and_retries(self, http):
        auth = make_auth(accept_user_agreement=True)
        http.add(responses.POST, ITEM, status=403, body=AGREEMENT_403)
        http.add(responses.POST, ITEM, status=201, json={"ok": True})
        http.add(responses.GET, API + "/authn/status", json=status_json())
        http.add(responses.PATCH, f"{API}/eperson/epersons/{PERSON}", json={"uuid": PERSON})
        http.add(responses.POST, API + "/authn/login", headers={"Authorization": "Bearer with-claim"})
        assert auth.session.post(ITEM).json() == {"ok": True}
        patch = next(c.request for c in http.calls if c.request.method == "PATCH")
        assert json.loads(patch.body) == [{"op": "add", "path": "/metadata/dspace.agreements.end-user",
                                           "value": [{"value": "true"}]}]
        assert http.calls[-1].request.headers["Authorization"] == "Bearer with-claim"

    def test_already_accepted_only_refreshes_and_replace_for_false(self, http):
        auth = make_auth(accept_user_agreement=True)
        http.add(responses.GET, API + "/authn/status",
                 json=status_json({"dspace.agreements.end-user": [{"value": "TRUE"}]}))
        http.add(responses.POST, API + "/authn/login", headers={"Authorization": "Bearer t"})
        assert auth.accept_user_agreement() is True
        assert not [c for c in http.calls if c.request.method == "PATCH"]

        http.replace(responses.GET, API + "/authn/status",
                     json=status_json({"dspace.agreements.end-user": [{"value": "false"}]}))
        http.add(responses.PATCH, f"{API}/eperson/epersons/{PERSON}", json={"uuid": PERSON})
        assert auth.accept_user_agreement() is True
        patch = [c.request for c in http.calls if c.request.method == "PATCH"][-1]
        assert json.loads(patch.body)[0]["op"] == "replace"

    def test_accept_failures(self, http):
        auth = make_auth(accept_user_agreement=True)
        http.add(responses.POST, ITEM, status=403, body=AGREEMENT_403)
        http.add(responses.GET, API + "/authn/status", json=status_json(uuid=None))
        assert auth.session.post(ITEM).status_code == 403          # no eperson -> not retried
        assert len([c for c in http.calls if c.request.url == ITEM]) == 1

        http.replace(responses.GET, API + "/authn/status", status=500)
        assert auth.accept_user_agreement() is False

        http.replace(responses.GET, API + "/authn/status", json=status_json())
        http.add(responses.PATCH, f"{API}/eperson/epersons/{PERSON}", status=422, body="nope")
        assert auth.accept_user_agreement() is False

        http.replace(responses.GET, API + "/authn/status", body=requests.exceptions.ConnectionError("down"))
        assert auth.accept_user_agreement() is False

        http.replace(responses.GET, API + "/authn/status", body="not json")
        assert auth.accept_user_agreement() is False

    def test_anonymous_session_is_not_handled(self, http):
        auth = make_auth(accept_user_agreement=True)
        auth._authenticated = False
        http.add(responses.GET, ITEM, status=403, body=AGREEMENT_403)
        assert auth.session.get(ITEM).status_code == 403
        assert len(http.calls) == 1
