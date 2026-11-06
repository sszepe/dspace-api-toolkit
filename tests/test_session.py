"""
Tests for long-run robustness: retries/backoff (session.py), token expiry,
refresh and re-login (auth.py) and the related config settings.
"""

import base64
import email.utils
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import responses

from dspace_api.auth import DSpaceAuth, jwt_expiry
from dspace_api.config import DSpaceConfig
from dspace_api.exceptions import AuthenticationError, NotFoundError
from dspace_api.items import ItemManager
from dspace_api.session import DSpaceSession, parse_retry_after

API = "http://localhost:8080/server/api"
URL = API + "/core/items/x"


def make_jwt(exp=None, **claims):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    payload = dict(claims, **({"exp": exp} if exp is not None else {}))
    return f"{b64({'alg': 'HS256'})}.{b64(payload)}.c2ln"


def make_auth(**settings):
    settings.setdefault("max_retries", 2)
    settings.setdefault("retry_backoff", 0.5)
    cfg = DSpaceConfig(API, all_languages=False, **settings)
    auth = DSpaceAuth(cfg)
    auth.session.sleep = Mock()
    auth._authenticated = True
    return auth


@pytest.fixture
def auth():
    return make_auth()


def login_flow(http, token="Bearer new-token", login_status=200):
    """Register what DSpaceAuth.login() needs: status, csrf, login."""
    http.add(responses.GET, API + "/authn/status", json={"authenticated": False},
             headers={"DSPACE-XSRF-TOKEN": "csrf-1"})
    http.add(responses.GET, API + "/security/csrf", headers={"DSPACE-XSRF-TOKEN": "csrf-2"})
    http.add(responses.POST, API + "/authn/login", status=login_status,
             headers={"Authorization": token} if login_status == 200 else {})


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfigSettings:
    def test_defaults(self, monkeypatch):
        for name in ("DSPACE_TIMEOUT", "DSPACE_MAX_RETRIES", "DSPACE_RETRY_BACKOFF", "DSPACE_RETRY_MAX_WAIT",
                     "DSPACE_RETRY_STATUSES", "DSPACE_TOKEN_REFRESH_MARGIN", "DSPACE_REAUTHENTICATE"):
            monkeypatch.delenv(name, raising=False)
        cfg = DSpaceConfig(API)
        assert (cfg.timeout, cfg.max_retries, cfg.retry_backoff, cfg.retry_max_wait) == (30, 3, 1.0, 30.0)
        assert cfg.retry_statuses == {429, 502, 503, 504}
        assert (cfg.token_refresh_margin, cfg.reauthenticate) == (60.0, True)

    def test_environment(self, monkeypatch):
        monkeypatch.setenv("DSPACE_TIMEOUT", "12.5")
        monkeypatch.setenv("DSPACE_MAX_RETRIES", "5")
        monkeypatch.setenv("DSPACE_RETRY_BACKOFF", "2")
        monkeypatch.setenv("DSPACE_RETRY_MAX_WAIT", "60")
        monkeypatch.setenv("DSPACE_RETRY_STATUSES", "429; 500,503")
        monkeypatch.setenv("DSPACE_TOKEN_REFRESH_MARGIN", "300")
        monkeypatch.setenv("DSPACE_REAUTHENTICATE", "false")
        cfg = DSpaceConfig(API)
        assert (cfg.timeout, cfg.max_retries, cfg.retry_backoff, cfg.retry_max_wait) == (12.5, 5, 2.0, 60.0)
        assert cfg.retry_statuses == {429, 500, 503}
        assert (cfg.token_refresh_margin, cfg.reauthenticate) == (300.0, False)

    def test_invalid_environment_values_fall_back(self, monkeypatch):
        monkeypatch.setenv("DSPACE_MAX_RETRIES", "many")
        monkeypatch.setenv("DSPACE_RETRY_BACKOFF", "slow")
        monkeypatch.setenv("DSPACE_RETRY_STATUSES", "5xx")
        cfg = DSpaceConfig(API)
        assert cfg.max_retries == 3 and cfg.retry_backoff == 1.0
        assert cfg.retry_statuses == {429, 502, 503, 504}

    def test_explicit_values_win_and_are_clamped(self, monkeypatch):
        monkeypatch.setenv("DSPACE_MAX_RETRIES", "9")
        cfg = DSpaceConfig(API, max_retries=-1, retry_backoff=-2, retry_max_wait=-1, token_refresh_margin=-5,
                           retry_statuses=[503], reauthenticate=False, timeout=5)
        assert (cfg.max_retries, cfg.retry_backoff, cfg.retry_max_wait, cfg.token_refresh_margin) == (0, 0, 0, 0)
        assert cfg.retry_statuses == {503} and cfg.reauthenticate is False and cfg.timeout == 5


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------

class TestRetryAfter:
    def test_seconds_and_empty(self):
        assert parse_retry_after("7") == 7.0
        assert parse_retry_after(" 0 ") == 0.0
        assert parse_retry_after(None) is None
        assert parse_retry_after("") is None
        assert parse_retry_after("soon") is None

    def test_http_date(self):
        now = 1_800_000_000
        future = email.utils.formatdate(now + 30, usegmt=True)
        past = email.utils.formatdate(now - 30, usegmt=True)
        assert parse_retry_after(future, now=now) == pytest.approx(30, abs=1)
        assert parse_retry_after(past, now=now) == 0.0
        assert parse_retry_after(email.utils.formatdate(time.time() + 60, usegmt=True)) > 0


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------

class TestRetries:
    def test_get_retried_on_503_with_exponential_backoff(self, auth, http):
        http.add(responses.GET, URL, status=503)
        http.add(responses.GET, URL, status=503)
        http.add(responses.GET, URL, json={"ok": True})
        assert auth.session.get(URL).json() == {"ok": True}
        assert len(http.calls) == 3
        waits = [c.args[0] for c in auth.session.sleep.call_args_list]
        assert 0.5 <= waits[0] <= 0.55 and 1.0 <= waits[1] <= 1.1      # base 0.5, doubled, <=10% jitter

    def test_gives_up_after_max_retries_and_returns_last_response(self, auth, http):
        http.add(responses.GET, URL, status=503)
        assert auth.session.get(URL).status_code == 503
        assert len(http.calls) == 3                                     # 1 + max_retries(2)

    def test_backoff_capped_by_retry_max_wait(self, http):
        auth = make_auth(retry_backoff=100, retry_max_wait=3)
        http.add(responses.GET, URL, status=502)
        auth.session.get(URL)
        assert all(c.args[0] == 3 for c in auth.session.sleep.call_args_list)

    def test_500_not_retried_by_default_but_configurable(self, http):
        auth = make_auth()
        http.add(responses.GET, URL, status=500)
        auth.session.get(URL)
        assert len(http.calls) == 1
        auth = make_auth(retry_statuses={500})
        auth.session.get(URL)
        assert len(http.calls) == 4

    def test_post_and_patch_not_retried_on_5xx(self, auth, http):
        http.add(responses.POST, URL, status=503)
        http.add(responses.PATCH, URL, status=504)
        assert auth.session.post(URL).status_code == 503
        assert auth.session.patch(URL).status_code == 504
        assert len(http.calls) == 2

    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    def test_put_and_delete_are_retried(self, auth, http, method):
        http.add(method, URL, status=503)
        http.add(method, URL, status=204)
        assert auth.session.request(method, URL).status_code == 204

    def test_429_retried_for_post_honouring_retry_after(self, auth, http):
        http.add(responses.POST, URL, status=429, headers={"Retry-After": "7"})
        http.add(responses.POST, URL, status=201, json={})
        assert auth.session.post(URL, json={"a": 1}).status_code == 201
        auth.session.sleep.assert_called_once_with(7.0)
        assert http.calls[1].request.body == b'{"a": 1}'                # body re-sent

    def test_retry_after_capped(self, http):
        auth = make_auth(retry_max_wait=10)
        http.add(responses.GET, URL, status=503, headers={"Retry-After": "3600"})
        http.add(responses.GET, URL, json={})
        auth.session.get(URL)
        auth.session.sleep.assert_called_once_with(10)

    def test_connection_error_retried_for_get(self, auth, http):
        http.add(responses.GET, URL, body=requests.exceptions.ConnectionError("reset"))
        http.add(responses.GET, URL, json={"ok": 1})
        assert auth.session.get(URL).json() == {"ok": 1}

    def test_connection_error_not_retried_for_post(self, auth, http):
        http.add(responses.POST, URL, body=requests.exceptions.ConnectionError("reset"))
        with pytest.raises(requests.exceptions.ConnectionError):
            auth.session.post(URL)
        assert len(http.calls) == 1

    def test_connect_timeout_retried_for_post(self, auth, http):
        http.add(responses.POST, URL, body=requests.exceptions.ConnectTimeout("no route"))
        http.add(responses.POST, URL, status=201)
        assert auth.session.post(URL).status_code == 201

    def test_read_timeout_exhausts_retries_and_raises(self, auth, http):
        http.add(responses.GET, URL, body=requests.exceptions.ReadTimeout("slow"))
        with pytest.raises(requests.exceptions.ReadTimeout):
            auth.session.get(URL)
        assert len(http.calls) == 3

    def test_session_without_auth_uses_defaults(self, http):
        session = DSpaceSession()
        session.sleep = Mock()
        http.add(responses.GET, URL, status=503)
        http.add(responses.GET, URL, json={})
        assert session.get(URL).status_code == 200
        assert session.dspace_auth is None

    def test_settings_fallback_for_config_without_retry_fields(self, http):
        fake_auth = Mock(spec=["config", "_update_token", "ensure_fresh_token", "can_reauthenticate"])
        fake_auth.config = SimpleNamespace()
        fake_auth.can_reauthenticate.return_value = False
        session = DSpaceSession(fake_auth)
        session.sleep = Mock()
        http.add(responses.GET, URL, status=503)
        session.get(URL)
        assert len(http.calls) == 4                                     # default max_retries=3

    def test_older_managers_get_retries_too(self, auth, http):
        """ItemManager calls session.get() directly and still gets the retry"""
        http.add(responses.GET, URL, status=503)
        http.add(responses.GET, URL, json={"uuid": "x"})
        assert ItemManager(auth).get_item("x") == {"uuid": "x"}


# ---------------------------------------------------------------------------
# CSRF retry, 401 re-login, internal scope
# ---------------------------------------------------------------------------

class TestAuthHandling:
    def test_csrf_403_retried_once_for_writes_only(self, auth, http):
        http.add(responses.POST, URL, status=403, headers={"DSPACE-XSRF-TOKEN": "fresh"})
        http.add(responses.POST, URL, status=201)
        assert auth.session.post(URL).status_code == 201
        assert http.calls[1].request.headers["X-XSRF-Token"] == "fresh"
        http.add(responses.GET, URL, status=403, headers={"DSPACE-XSRF-TOKEN": "t"})
        assert auth.session.get(URL).status_code == 403
        assert len(http.calls) == 3

    def test_401_triggers_relogin_and_one_retry(self, auth, http):
        auth._username, auth._password = "u", "p"
        auth.session.headers["Authorization"] = "Bearer dead"
        http.add(responses.GET, URL, status=401)
        http.add(responses.GET, URL, json={"ok": True})
        login_flow(http)
        assert auth.session.get(URL).json() == {"ok": True}
        retried = [c.request for c in http.calls if c.request.url == URL][-1]
        assert retried.headers["Authorization"] == "Bearer new-token"
        status_call = next(c.request for c in http.calls if c.request.url.endswith("/authn/status"))
        assert "Authorization" not in status_call.headers               # dead token dropped first
        assert auth.is_authenticated

    def test_401_without_credentials_is_returned(self, auth, http):
        http.add(responses.GET, URL, status=401)
        assert auth.session.get(URL).status_code == 401
        assert len(http.calls) == 1

    def test_failed_relogin_raises(self, auth, http):
        auth._username, auth._password = "u", "wrong"
        http.add(responses.GET, URL, status=401)
        login_flow(http, login_status=401)
        with pytest.raises(AuthenticationError, match="Invalid credentials"):
            auth.session.get(URL)

    def test_manager_sees_error_if_still_401_after_relogin(self, auth, http):
        auth._username, auth._password = "u", "p"
        http.add(responses.GET, URL, status=401)
        login_flow(http)
        from dspace_api.exceptions import AuthenticationError as AuthErr
        with pytest.raises(AuthErr):
            ItemManager(auth).get_item("x")

    def test_internal_requests_skip_refresh_but_still_retry(self, auth, http):
        auth.ensure_fresh_token = Mock()
        http.add(responses.GET, URL, status=503)
        http.add(responses.GET, URL, json={})
        with auth.session.internal():
            with auth.session.internal():                               # nesting
                auth.session.get(URL)
            assert auth.session._is_internal()
        assert not auth.session._is_internal()
        auth.ensure_fresh_token.assert_not_called()
        assert len(http.calls) == 2

    def test_fresh_token_checked_before_each_request(self, auth, http):
        auth.ensure_fresh_token = Mock()
        http.add(responses.GET, URL, json={})
        auth.session.get(URL)
        auth.ensure_fresh_token.assert_called_once()

    def test_session_detached_from_collected_auth(self, http):
        auth = make_auth()
        session = auth.session
        del auth
        import gc
        gc.collect()
        assert session.dspace_auth is None
        session.sleep = Mock()
        http.add(responses.GET, URL, json={})
        assert session.get(URL).status_code == 200


# ---------------------------------------------------------------------------
# Token expiry and refresh (auth.py)
# ---------------------------------------------------------------------------

class TestJwtExpiry:
    def test_values(self):
        assert jwt_expiry("Bearer " + make_jwt(exp=1_900_000_000)) == 1_900_000_000.0
        assert jwt_expiry(make_jwt(exp=5)) == 5.0                       # no "Bearer " prefix
        assert jwt_expiry("Bearer " + make_jwt(sub="x")) is None        # no exp claim
        assert jwt_expiry("Bearer opaque-token") is None
        assert jwt_expiry("Bearer a.!!!.c") is None                     # not base64/json
        assert jwt_expiry("Bearer a." + base64.urlsafe_b64encode(b'"str"').decode() + ".c") is None
        assert jwt_expiry(None) is None and jwt_expiry("") is None


class TestTokenLifecycle:
    def test_login_stores_credentials_and_expiry(self, http):
        auth = make_auth()
        auth._authenticated = False
        exp = time.time() + 1800
        login_flow(http, token="Bearer " + make_jwt(exp=exp))
        auth.login("u", "p")
        assert auth.token_expires_at == pytest.approx(exp)
        assert auth.can_reauthenticate()

    def test_reauthenticate_disabled_keeps_no_credentials(self, http):
        auth = make_auth(reauthenticate=False)
        login_flow(http)
        auth.login("u", "p")
        assert auth._username is None and not auth.can_reauthenticate()

    def test_can_reauthenticate_respects_config_at_runtime(self, auth):
        auth._username, auth._password = "u", "p"
        assert auth.can_reauthenticate()
        auth.config.reauthenticate = False
        assert not auth.can_reauthenticate()

    def test_logout_forgets_credentials(self, auth, http):
        auth._username, auth._password, auth._token_expires_at = "u", "p", 1.0
        http.add(responses.POST, API + "/authn/logout", status=204)
        auth.logout()
        assert (auth._username, auth._password, auth.token_expires_at) == (None, None, None)
        assert isinstance(auth.session, DSpaceSession)

    @pytest.mark.parametrize("authenticated,expires", [(False, 1.0), (True, None), (True, 9e12)])
    def test_ensure_fresh_token_noop(self, auth, authenticated, expires):
        auth._authenticated, auth._token_expires_at = authenticated, expires
        auth.refresh_token = Mock()
        auth.ensure_fresh_token()
        auth.refresh_token.assert_not_called()

    def test_ensure_fresh_token_refreshes_within_margin(self, auth):
        auth._token_expires_at = time.time() + 30                       # margin is 60s
        auth.refresh_token = Mock()
        auth.ensure_fresh_token()
        auth.refresh_token.assert_called_once()

    def test_ensure_fresh_token_skips_if_another_thread_refreshed(self, auth):
        auth._token_expires_at = time.time() + 30
        auth.refresh_token = Mock()

        class RefreshedMeanwhile:
            def __enter__(self):
                auth._token_expires_at = time.time() + 3600
            def __exit__(self, *exc):
                return False

        auth._reauth_lock = RefreshedMeanwhile()
        auth.ensure_fresh_token()
        auth.refresh_token.assert_not_called()

    def test_refresh_token_success(self, auth, http):
        exp = time.time() + 1800
        http.add(responses.POST, API + "/authn/login", status=200,
                 headers={"Authorization": "Bearer " + make_jwt(exp=exp)})
        auth.reauthenticate = Mock()
        assert auth.refresh_token() is True
        assert auth.token_expires_at == pytest.approx(exp)
        assert http.calls[0].request.body is None                       # no credentials sent
        auth.reauthenticate.assert_not_called()

    @pytest.mark.parametrize("response", [
        {"status": 401},
        {"status": 200},                                                # no Authorization header
        {"body": requests.exceptions.ConnectionError("down")},
    ])
    def test_refresh_token_falls_back_to_login(self, auth, http, response):
        http.add(responses.POST, API + "/authn/login", **response)
        auth.reauthenticate = Mock(return_value=True)
        assert auth.refresh_token() is True
        auth.reauthenticate.assert_called_once()

    def test_reauthenticate_without_credentials(self, auth):
        with pytest.raises(AuthenticationError, match="no stored credentials"):
            auth.reauthenticate()

    def test_proactive_refresh_before_a_request(self, auth, http):
        auth._username, auth._password = "u", "p"
        auth._token_expires_at = time.time() + 10
        http.add(responses.POST, API + "/authn/login", status=200,
                 headers={"Authorization": "Bearer " + make_jwt(exp=time.time() + 1800)})
        http.add(responses.GET, URL, json={})
        auth.session.get(URL)
        assert [c.request.method for c in http.calls] == ["POST", "GET"]
        assert http.calls[1].request.headers["Authorization"].startswith("Bearer ")

    def test_internal_scope_with_non_dspace_session(self, auth):
        """check_status still works when a test double replaces the session"""
        auth.session = Mock()
        auth.session.get.return_value = Mock(status_code=200, headers={},
                                             json=Mock(return_value={"authenticated": True}))
        assert auth.check_status() is True
