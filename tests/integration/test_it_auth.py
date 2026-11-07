"""Authentication: login, re-login on 401, proactive token refresh"""

import time

import pytest

from dspace_api.epersons import EPersonManager

from .helpers import unsupported


@pytest.mark.feature("auth.login")
def test_login_and_current_user(auth, epersons, it_config):
    assert auth.is_authenticated
    status = epersons.get_auth_status()
    assert status.get("authenticated") is True
    me = epersons.get_current_user()
    assert me and me["email"].lower() == it_config["user"].lower()


@pytest.mark.feature("auth.relogin")
def test_relogin_after_invalid_token(new_auth, me):
    auth = new_auth()
    auth.session.headers["Authorization"] = "Bearer invalid.token.value"
    # An admin-only read: with a broken token DSpace answers 401 -> re-login -> retry
    person = EPersonManager(auth).get_eperson(me["uuid"])
    assert person["uuid"] == me["uuid"]
    assert auth.session.headers["Authorization"] != "Bearer invalid.token.value"


@pytest.mark.feature("auth.refresh")
def test_token_refreshed_before_expiry(new_auth, me):
    auth = new_auth()
    if auth.token_expires_at is None:
        unsupported("login token has no readable expiry (not a plain JWT)")
    old_token = auth.session.headers.get("Authorization")
    auth._token_expires_at = time.time()          # pretend it is about to expire
    person = EPersonManager(auth).get_eperson(me["uuid"])
    assert person["uuid"] == me["uuid"]
    assert auth.token_expires_at and auth.token_expires_at > time.time() + 60
    assert auth.session.headers.get("Authorization")
    assert auth.is_authenticated
    print("token changed:", auth.session.headers.get("Authorization") != old_token)
