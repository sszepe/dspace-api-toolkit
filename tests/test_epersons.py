"""
Tests for DSpace eperson management (epersons.py)
"""

import json

import pytest
import responses

from dspace_api.epersons import END_USER_AGREEMENT, IGNORE_END_USER_AGREEMENT, EPersonManager
from dspace_api.exceptions import NotFoundError

E1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
G1 = "11111111-1111-1111-1111-111111111111"
PERSON = f"/eperson/epersons/{E1}"


@pytest.fixture
def mgr(http_auth):
    return EPersonManager(http_auth)


def last(http):
    return http.calls[-1].request


def person(agreement=None, **md):
    metadata = {f"eperson.{k}": [{"value": v}] for k, v in md.items()}
    if agreement is not None:
        metadata[END_USER_AGREEMENT] = [{"value": agreement}]
    return {"uuid": E1, "email": "jane@example.org", "metadata": metadata}


class TestLookup:
    def test_get_and_iter(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(PERSON), json=person())
        http.add(responses.GET, api_url("/eperson/epersons"), json=hal_page("epersons", [person()]))
        assert mgr.get_eperson(E1)["uuid"] == E1
        assert len(list(mgr.iter_epersons())) == 1

    def test_find_by_email(self, mgr, http, api_url):
        url = api_url("/eperson/epersons/search/byEmail")
        http.add(responses.GET, url, json=person())
        assert mgr.find_by_email("jane@example.org")["uuid"] == E1
        assert last(http).params == {"email": "jane@example.org"}
        http.replace(responses.GET, url, status=204)
        assert mgr.find_by_email("nobody@example.org") is None

    def test_searches(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/eperson/epersons/search/byMetadata"), json=hal_page("epersons", [person()]))
        http.add(responses.GET, api_url("/eperson/epersons/search/isNotMemberOf"), json=hal_page("epersons", []))
        assert len(mgr.search_epersons("doe", max_results=5)) == 1
        assert last(http).params["query"] == "doe"
        mgr.search_epersons_not_member_of(G1, "doe")
        assert last(http).params["group"] == G1 and last(http).params["query"] == "doe"

    def test_resolve(self, mgr, http, api_url):
        assert mgr.resolve_eperson(E1) == E1
        assert mgr.resolve_eperson({"uuid": E1}) == E1
        url = api_url("/eperson/epersons/search/byEmail")
        http.add(responses.GET, url, json=person())
        assert mgr.resolve_eperson("jane@example.org") == E1
        http.replace(responses.GET, url, status=204)
        with pytest.raises(NotFoundError):
            mgr.resolve_eperson("ghost@example.org")
        with pytest.raises(ValueError):
            mgr.resolve_eperson("Jane Doe")

    def test_groups(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url(f"{PERSON}/groups"), json=hal_page("groups", [{"uuid": G1}]))
        assert mgr.get_eperson_groups(E1) == [{"uuid": G1}]
        assert last(http).params["embed"] == "object"
        mgr.get_eperson_groups(E1, embed_object=False)
        assert "embed" not in last(http).params


class TestCurrentUser:
    def test_auth_status(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/authn/status"), json={"authenticated": False})
        mgr.get_auth_status()
        assert last(http).params == {"embed": "eperson"}
        mgr.get_auth_status(embed_eperson=False)
        assert last(http).params == {}

    def test_current_user_variants(self, mgr, http, api_url):
        url = api_url("/authn/status")
        http.add(responses.GET, url, json={"authenticated": False})
        assert mgr.get_current_user() is None
        http.replace(responses.GET, url, json={"authenticated": True, "_embedded": {"eperson": {"uuid": E1}}})
        assert mgr.get_current_user() == {"uuid": E1}
        http.replace(responses.GET, url, json={"authenticated": True,
                                               "_links": {"eperson": {"href": api_url(PERSON)}}})
        http.add(responses.GET, api_url(PERSON), json={"uuid": E1, "via": "href"})
        assert mgr.get_current_user()["via"] == "href"
        http.replace(responses.GET, url, json={"authenticated": True})
        assert mgr.get_current_user() is None


class TestWrite:
    def test_create_eperson(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/eperson/epersons"), status=201, json={"uuid": E1})
        mgr.create_eperson("jane@example.org", "Jane", "Doe", language="de", net_id="jdoe",
                           metadata={"eperson.phone": "123", "eperson.firstname": "Janet"})
        body = json.loads(last(http).body)
        assert body["email"] == body["name"] == "jane@example.org"
        assert body["netid"] == "jdoe" and body["canLogIn"] is True and body["selfRegistered"] is False
        assert body["metadata"] == {"eperson.phone": [{"value": "123"}], "eperson.firstname": [{"value": "Janet"}],
                                    "eperson.lastname": [{"value": "Doe"}], "eperson.language": [{"value": "de"}]}
        mgr.create_eperson("x@example.org")
        body = json.loads(last(http).body)
        assert "netid" not in body and body["metadata"] == {}

    def test_get_or_create(self, mgr, http, api_url):
        url = api_url("/eperson/epersons/search/byEmail")
        http.add(responses.GET, url, json=person())
        assert mgr.get_or_create_eperson("jane@example.org")["uuid"] == E1
        http.replace(responses.GET, url, status=204)
        http.add(responses.POST, api_url("/eperson/epersons"), status=201, json={"uuid": "new"})
        assert mgr.get_or_create_eperson("new@example.org", first_name="N")["uuid"] == "new"

    def test_create_from_token(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/eperson/epersons"), status=201, json={"uuid": E1})
        mgr.create_eperson_from_token("tok", "Jane", "Doe", password="s3cret!", email="jane@example.org")
        assert last(http).params == {"token": "tok"}
        body = json.loads(last(http).body)
        assert body["password"] == "s3cret!" and body["email"] == "jane@example.org"
        mgr.create_eperson_from_token("tok", "Jane", "Doe")
        body = json.loads(last(http).body)
        assert "password" not in body and "email" not in body

    @pytest.mark.parametrize("call,expected", [
        (lambda m: m.set_can_log_in(E1, False), {"op": "replace", "path": "/canLogin", "value": "false"}),
        (lambda m: m.set_require_certificate(E1, True), {"op": "replace", "path": "/certificate", "value": "true"}),
        (lambda m: m.set_email(E1, "n@x.org"), {"op": "replace", "path": "/email", "value": "n@x.org"}),
        (lambda m: m.set_netid(E1, "nid"), {"op": "replace", "path": "/netid", "value": "nid"}),
        (lambda m: m.set_password(E1, "new", current_password="old"),
         {"op": "add", "path": "/password", "value": {"new_password": "new", "current_password": "old"}}),
        (lambda m: m.update_eperson_metadata(E1, "add", {"eperson.phone": "1"}),
         {"op": "add", "path": "/metadata/eperson.phone", "value": [{"value": "1"}]}),
    ])
    def test_patch_operations(self, mgr, http, api_url, call, expected):
        http.add(responses.PATCH, api_url(PERSON), json={"uuid": E1})
        call(mgr)
        assert json.loads(last(http).body) == [expected]
        assert last(http).params == {}

    def test_password_reset_with_token(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url(PERSON), json={"uuid": E1})
        mgr.set_password(E1, "new", token="tok")
        assert last(http).params == {"token": "tok"}
        assert json.loads(last(http).body)[0]["value"] == {"new_password": "new"}

    def test_upsert_metadata(self, mgr, http, api_url):
        http.add(responses.GET, api_url(PERSON), json=person(firstname="Jane"))
        http.add(responses.PATCH, api_url(PERSON), json={"uuid": E1})
        mgr.upsert_eperson_metadata(E1, {"eperson.firstname": "Janet", "eperson.phone": "1"})
        assert [o["op"] for o in json.loads(last(http).body)] == ["replace", "add"]

    def test_delete(self, mgr, http, api_url):
        http.add(responses.DELETE, api_url(PERSON), status=204)
        assert mgr.delete_eperson(E1) is True


class TestRegistrations:
    def test_request_registration(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/eperson/registrations"), status=201)
        assert mgr.request_registration("j@x.org", "forgot") is True
        assert last(http).params == {"accountRequestType": "forgot"}
        assert json.loads(last(http).body) == {"email": "j@x.org"}
        with pytest.raises(ValueError):
            mgr.request_registration("j@x.org", "reset")

    def test_find_by_token(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/eperson/registrations/search/findByToken"), json={"id": 1, "email": "e"})
        assert mgr.find_registration_by_token("tok")["id"] == 1
        assert last(http).params == {"token": "tok"}

    def test_update_registration_email(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url("/eperson/registrations/3"), status=204)
        assert mgr.update_registration_email(3, "tok", "n@x.org", operation="add") is True
        assert json.loads(last(http).body) == [{"op": "add", "path": "/email", "value": ["n@x.org"]}]
        with pytest.raises(ValueError):
            mgr.update_registration_email(3, "tok", "n@x.org", operation="remove")

    def test_merge_registration(self, mgr, http, api_url):
        http.add(responses.POST, api_url(PERSON), status=201, json={"uuid": E1})
        assert mgr.merge_registration(E1, "tok", override=["eperson.firstname", "email"]) == {"uuid": E1}
        assert last(http).params == {"token": "tok", "override": "eperson.firstname,email"}
        http.replace(responses.POST, api_url(PERSON), status=201, body=b"")
        assert mgr.merge_registration(E1, "tok") == {}
        assert last(http).params == {"token": "tok"}


class TestEndUserAgreement:
    def test_has_accepted_from_dict_without_request(self, mgr, http):
        assert mgr.has_accepted_end_user_agreement(person("true")) is True
        assert mgr.has_accepted_end_user_agreement(person("TRUE")) is True
        assert mgr.has_accepted_end_user_agreement(person("false")) is False
        assert mgr.has_accepted_end_user_agreement(person()) is False
        assert len(http.calls) == 0

    def test_has_accepted_fetches_by_uuid(self, mgr, http, api_url):
        http.add(responses.GET, api_url(PERSON), json=person("true"))
        assert mgr.has_accepted_end_user_agreement(E1) is True
        assert mgr.has_accepted_end_user_agreement({"uuid": E1}) is True   # dict without metadata
        assert len(http.calls) == 2

    @staticmethod
    def status(http, api_url, eperson=E1):
        links = {"eperson": {"href": api_url(f"/eperson/epersons/{eperson}")}} if eperson else {}
        http.add(responses.GET, api_url("/authn/status"), json={"authenticated": True, "_links": links})

    @staticmethod
    def last_patch(http):
        return json.loads([c.request for c in http.calls if c.request.method == "PATCH"][-1].body)

    def test_accept(self, mgr, http, api_url):
        http.add(responses.GET, api_url(PERSON), json=person())
        http.add(responses.PATCH, api_url(PERSON), json={"uuid": E1})
        self.status(http, api_url, eperson="other")
        mgr.accept_end_user_agreement(E1)
        assert self.last_patch(http) == [{"op": "add", "path": f"/metadata/{END_USER_AGREEMENT}",
                                          "value": [{"value": "true"}]}]
        http.replace(responses.GET, api_url(PERSON), json=person("false"))
        mgr.accept_end_user_agreement(E1)
        assert self.last_patch(http)[0]["op"] == "replace"   # no duplicate value

    def test_accept_for_current_user_refreshes_token(self, mgr, http, api_url, http_auth, monkeypatch):
        refreshed = []
        monkeypatch.setattr(http_auth, "refresh_token", lambda: refreshed.append(1) or True, raising=False)
        monkeypatch.setattr(type(http_auth), "is_authenticated", property(lambda self: True), raising=False)
        http.add(responses.GET, api_url(PERSON), json=person())
        http.add(responses.PATCH, api_url(PERSON), json={"uuid": E1})
        self.status(http, api_url)
        mgr.accept_end_user_agreement(E1)
        assert refreshed == [1]

    def test_no_refresh_for_anonymous_or_without_refresh(self, mgr, http_auth, monkeypatch):
        monkeypatch.setattr(type(http_auth), "is_authenticated", property(lambda self: False), raising=False)
        assert mgr._refresh_token_if_current(E1) is False
        mgr.auth = object()
        assert mgr._refresh_token_if_current(E1) is False

    def test_accept_is_noop_when_already_accepted(self, mgr, http, api_url):
        http.add(responses.GET, api_url(PERSON), json=person("true"))
        assert mgr.accept_end_user_agreement(E1)["uuid"] == E1
        assert [c.request.method for c in http.calls] == ["GET"]

    def test_revoke(self, mgr, http, api_url):
        http.add(responses.GET, api_url(PERSON), json=person())
        assert mgr.revoke_end_user_agreement(E1)["uuid"] == E1
        assert [c.request.method for c in http.calls] == ["GET"]
        http.replace(responses.GET, api_url(PERSON), json=person("true"))
        http.add(responses.PATCH, api_url(PERSON), json={"uuid": E1})
        self.status(http, api_url, eperson=None)
        mgr.revoke_end_user_agreement(E1)
        assert self.last_patch(http) == [{"op": "remove", "path": f"/metadata/{END_USER_AGREEMENT}"}]

    def test_ignore_agreement(self, mgr, http, api_url):
        flagged = {"uuid": E1, "metadata": {IGNORE_END_USER_AGREEMENT: [{"value": "true"}]}}
        assert mgr.is_ignoring_end_user_agreement(flagged) is True
        assert mgr.is_ignoring_end_user_agreement(person()) is False
        http.add(responses.GET, api_url(PERSON), json=person())
        http.add(responses.PATCH, api_url(PERSON), json={"uuid": E1})
        self.status(http, api_url, eperson="other")
        mgr.ignore_end_user_agreement(E1)
        assert self.last_patch(http) == [{"op": "add", "path": f"/metadata/{IGNORE_END_USER_AGREEMENT}",
                                          "value": [{"value": "true"}]}]
        assert mgr.is_ignoring_end_user_agreement(E1) is False
        http.replace(responses.GET, api_url(PERSON), json=flagged)
        mgr.ignore_end_user_agreement(E1, ignore=False)
        assert self.last_patch(http) == [{"op": "remove", "path": f"/metadata/{IGNORE_END_USER_AGREEMENT}"}]

