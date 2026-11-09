"""
"Login as" another user: X-On-Behalf-Of (session.py, on_behalf.py, exceptions.py)
"""

import threading
from unittest.mock import Mock

import pytest
import responses

from dspace_api import DSpaceClient
from dspace_api.auth import DSpaceAuth
from dspace_api.config import DSpaceConfig
from dspace_api.exceptions import AuthorizationError, BadRequestError, OnBehalfOfError, handle_response_error
from dspace_api.items import ItemManager
from dspace_api.on_behalf import explain_refusal, on_behalf_of, verify_on_behalf_of
from dspace_api.session import ON_BEHALF_OF_HEADER

API = "http://localhost:8080/server/api"
JANE = "11111111-1111-1111-1111-111111111111"
ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SITE = "55555555-5555-5555-5555-555555555555"
ITEM = f"{API}/core/items/i1"
STATUS = f"{API}/authn/status"
INVALID = '{"status": 400, "message": "Authentication request is invalid or incorrect"}'


def make_auth():
    auth = DSpaceAuth(DSpaceConfig(API, all_languages=False, max_retries=0, env_file=False))
    auth.session.sleep = Mock()
    auth._authenticated = True
    return auth


@pytest.fixture
def auth():
    return make_auth()


def status_as(uuid):
    return {"authenticated": True, "_links": {"eperson": {"href": f"{API}/eperson/epersons/{uuid}"}}}


def header_of(call):
    return call.request.headers.get(ON_BEHALF_OF_HEADER)


# ---------------------------------------------------------------------------
# Session: the header
# ---------------------------------------------------------------------------

class TestSessionHeader:
    def test_only_inside_the_block_and_nested(self, auth, http):
        http.add(responses.GET, ITEM, json={})
        session = auth.session
        session.get(ITEM)
        with session.on_behalf_of(JANE) as same:
            assert same is session and session.acting_as == JANE
            session.get(ITEM, headers={"Accept": "application/json"})
            with session.on_behalf_of(None):
                session.get(ITEM)
            with session.on_behalf_of(ADMIN):
                session.get(ITEM)
            session.get(ITEM)
        session.get(ITEM)
        assert [header_of(c) for c in http.calls] == [None, JANE, None, ADMIN, JANE, None]
        assert http.calls[1].request.headers["Accept"] == "application/json"
        assert session.acting_as is None

    def test_reset_after_an_exception(self, auth):
        with pytest.raises(RuntimeError):
            with auth.session.on_behalf_of(JANE):
                raise RuntimeError("boom")
        assert auth.session.acting_as is None

    def test_toolkit_requests_never_carry_it(self, auth, http):
        http.add(responses.POST, f"{API}/authn/login", headers={"Authorization": "Bearer new"})
        http.add(responses.GET, ITEM, json={})
        with auth.session.on_behalf_of(JANE):
            auth.refresh_token()                                  # login request
            with auth.session.internal():                         # e.g. status, CSRF, projection checks
                auth.session.get(ITEM)
        assert [header_of(c) for c in http.calls] == [None, None]

    def test_other_threads_and_sessions_are_not_affected(self, auth, http):
        http.add(responses.GET, ITEM, json={})
        other = make_auth()
        seen = []
        with auth.session.on_behalf_of(JANE):
            worker = threading.Thread(target=lambda: seen.append(auth.session.acting_as))
            worker.start()
            worker.join()
            other.session.get(ITEM)
        assert seen == [None] and header_of(http.calls[0]) is None

    def test_older_managers_get_it_too(self, auth, http):
        http.add(responses.GET, ITEM, json={"uuid": "i1"})
        with auth.session.on_behalf_of(JANE):
            ItemManager(auth).get_item("i1")
        assert header_of(http.calls[0]) == JANE

    def test_response_is_marked(self, auth, http):
        http.add(responses.GET, ITEM, json={})
        assert auth.session.get(ITEM).dspace_on_behalf_of is None
        with auth.session.on_behalf_of(JANE):
            assert auth.session.get(ITEM).dspace_on_behalf_of == JANE


# ---------------------------------------------------------------------------
# Errors inside a block
# ---------------------------------------------------------------------------

class TestErrors:
    def response(self, status, text, acting_as=JANE):
        return Mock(status_code=status, text=text, dspace_on_behalf_of=acting_as)

    def test_filter_refusal_becomes_on_behalf_error(self):
        with pytest.raises(OnBehalfOfError, match="switched off") as info:
            handle_response_error(self.response(400, INVALID), "Get item")
        assert info.value.status_code == 400
        with pytest.raises(OnBehalfOfError, match="logged-in administrator"):
            handle_response_error(self.response(401, ""), "Get item")

    def test_other_errors_keep_their_class(self):
        with pytest.raises(BadRequestError):                         # a normal 400 in the block
            handle_response_error(self.response(400, '{"message": "bad sort"}'), "Search")
        with pytest.raises(AuthorizationError) as info:              # Jane may not do this
            handle_response_error(self.response(403, "Access is denied"), "Get item")
        assert not isinstance(info.value, OnBehalfOfError)
        with pytest.raises(BadRequestError):                         # no header sent
            handle_response_error(self.response(400, INVALID, acting_as=None), "x")
        with pytest.raises(BadRequestError):                         # text not a string (mocks)
            handle_response_error(Mock(status_code=400, text=None, dspace_on_behalf_of=JANE), "x")


# ---------------------------------------------------------------------------
# on_behalf_of(): resolve, verify, explain
# ---------------------------------------------------------------------------

class TestOnBehalfOf:
    def test_verified_block(self, auth, http):
        http.add(responses.GET, STATUS, json=status_as(JANE))
        http.add(responses.GET, ITEM, json={})
        with on_behalf_of(auth, JANE) as uuid:
            assert uuid == JANE
            auth.session.get(ITEM)
        assert [header_of(c) for c in http.calls] == [JANE, JANE]
        assert auth.session.acting_as is None

    def test_resolves_email_and_dict_and_none(self, auth, http):
        http.add(responses.GET, f"{API}/eperson/epersons/search/byEmail", json={"uuid": JANE})
        with on_behalf_of(auth, "jane@example.org", verify=False) as uuid:
            assert uuid == JANE
        assert header_of(http.calls[0]) is None                     # lookup without the header
        with on_behalf_of(auth, {"uuid": JANE}, verify=False):
            with on_behalf_of(auth, None) as nothing:
                assert nothing is None and auth.session.acting_as is None
            assert auth.session.acting_as == JANE

    def test_client_method(self, http, monkeypatch):
        client = DSpaceClient(API)
        client.auth._authenticated = True
        http.add(responses.GET, STATUS, json=status_as(JANE))
        with client.on_behalf_of(JANE):
            assert client.auth.session.acting_as == JANE
        with client.on_behalf_of(JANE, verify=False):
            pass
        assert len(http.calls) == 1

    def test_needs_a_dspace_session(self):
        with pytest.raises(TypeError):
            with on_behalf_of(Mock(session=object()), JANE):
                pass

    def test_no_effect_is_reported(self, auth, http):
        http.add(responses.GET, STATUS, json=status_as(ADMIN))
        with pytest.raises(OnBehalfOfError, match=f"no effect.*{ADMIN}"):
            with auth.session.on_behalf_of(JANE):
                verify_on_behalf_of(auth, JANE)
        http.replace(responses.GET, STATUS, body=b"")
        with pytest.raises(OnBehalfOfError, match="no user"):
            with auth.session.on_behalf_of(JANE):
                verify_on_behalf_of(auth, JANE)

    @pytest.mark.parametrize("status,reason", [(401, "log in as an administrator"),
                                               (403, "only administrators"), (500, "unexpected")])
    def test_refusal_without_lookup(self, auth, http, status, reason):
        http.add(responses.GET, STATUS, status=status)
        with pytest.raises(OnBehalfOfError, match=reason) as info:
            with on_behalf_of(auth, JANE):
                pytest.fail("block must not run")
        assert info.value.status_code == status and auth.session.acting_as is None

    def site_and_authz(self, http, admin):
        http.add(responses.GET, f"{API}/core/sites", json={"_embedded": {"sites": [{"uuid": SITE}]}})
        found = [{"id": f"{JANE}_administratorOf_core.site_{SITE}"}] if admin else []
        http.add(responses.GET, f"{API}/authz/authorizations/search/object",
                 json={"_embedded": {"authorizations": found}, "page": {"totalPages": 1}})

    def test_400_explained_feature_off(self, auth, http):
        http.add(responses.GET, STATUS, status=400, body=INVALID)
        http.add(responses.GET, f"{API}/eperson/epersons/{JANE}", json={"uuid": JANE, "email": "jane@x.org"})
        self.site_and_authz(http, admin=False)
        with pytest.raises(OnBehalfOfError, match="webui.user.assumelogin = true"):
            with on_behalf_of(auth, JANE):
                pass
        lookups = [c for c in http.calls if "/authn/status" not in c.request.url]
        assert lookups and all(header_of(c) is None for c in lookups)  # diagnosis runs as the admin

    def test_400_explained_target_is_admin(self, auth, http):
        http.add(responses.GET, f"{API}/eperson/epersons/{JANE}", json={"uuid": JANE, "email": "boss@x.org"})
        self.site_and_authz(http, admin=True)
        assert "boss@x.org is an administrator" in explain_refusal(auth, JANE, 400)

    def test_400_explained_unknown_eperson(self, auth, http):
        http.add(responses.GET, f"{API}/eperson/epersons/{JANE}", status=404)
        assert f"there is no eperson {JANE}" in explain_refusal(auth, JANE, 400)

    def test_diagnosis_lookups_may_fail(self, auth, http):
        http.add(responses.GET, f"{API}/eperson/epersons/{JANE}", status=500)
        http.add(responses.GET, f"{API}/core/sites", status=500)
        assert "switched off" in explain_refusal(auth, JANE, 400)
