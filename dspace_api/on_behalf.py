# -*- coding: UTF-8 -*-
"""
"Login as" another user: the X-On-Behalf-Of header (DSpace 7+, DSpace-CRIS).

An administrator's session can send requests as if another eperson made
them - DSpace then checks that eperson's permissions, records them as
submitter, and so on. Useful to check what a user can see or do, or to
submit on their behalf:

    with client.on_behalf_of("jane@example.org"):
        client.items.get_item(uuid)                 # 403 if Jane may not read it
        client.workspace.submit(collection, {...})  # Jane is the submitter

Server requirements (StatelessAuthenticationFilter):

    webui.user.assumelogin = true     in local.cfg (off in plain DSpace, on in DSpace-CRIS)
    the logged-in user is an administrator
    the target is not an administrator  (DSpace-CRIS: allowed with
                                         webui.user.assumelogin.admin = true)

DSpace answers every violation with the same "400 Authentication request is
invalid or incorrect" (403 for a non-admin caller, 401 without a login), so
on_behalf_of() checks once on entering the block and raises OnBehalfOfError
with the actual reason. verify=False skips that request.

Only the requests of this session in the current thread / asyncio task carry
the header; the toolkit's own login, token refresh and status requests never
do. Threads started inside the block do not inherit it.
"""

import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional, Union

from .config import Endpoints
from .exceptions import NotFoundError, OnBehalfOfError
from .session import ON_BEHALF_OF_HEADER

logger = logging.getLogger(__name__)

EPersonRef = Union[str, Dict[str, Any]]


def _session_of(auth):
    session = getattr(auth, "session", None)
    if session is None or not hasattr(session, "on_behalf_of"):
        raise TypeError("on_behalf_of() needs a DSpaceAuth whose session is a DSpaceSession")
    return session


@contextmanager
def on_behalf_of(auth, eperson: Optional[EPersonRef], verify: bool = True) -> Iterator[str]:
    """
    Send this session's requests in the block as `eperson` (UUID, email,
    href or eperson dict). Yields the eperson UUID. eperson=None switches an
    outer block off for the inner one.
    """
    session = _session_of(auth)
    if eperson is None:
        with session.on_behalf_of(None):
            yield None
        return
    from .epersons import EPersonManager
    epersons = EPersonManager(auth)
    uuid = epersons.resolve_eperson(eperson)
    with session.on_behalf_of(uuid):
        if verify:
            verify_on_behalf_of(auth, uuid)
        logger.info("Acting on behalf of eperson %s", uuid)
        yield uuid


def verify_on_behalf_of(auth, uuid: str) -> Dict[str, Any]:
    """
    GET /authn/status with the header and check that the server now sees
    `uuid` as the current user. Raises OnBehalfOfError with the reason.
    Must be called inside session.on_behalf_of(uuid).
    """
    session = _session_of(auth)
    url = auth.config.dspace_url + Endpoints.AUTHN_STATUS
    response = session.get(url, timeout=auth.config.timeout, headers={"Accept": "application/json"})
    if response.status_code == 200:
        status = response.json() if response.content else {}
        href = ((status.get("_links") or {}).get("eperson") or {}).get("href") or ""
        if status.get("authenticated") and href.rstrip("/").endswith(uuid):
            return status
        raise OnBehalfOfError(
            f"'login as' {uuid} had no effect: the server reports "
            f"{href.rsplit('/', 1)[-1] or 'no user'} as current user", status_code=200,
            response_text=response.text)
    raise OnBehalfOfError(explain_refusal(auth, uuid, response.status_code),
                          status_code=response.status_code, response_text=response.text)


def explain_refusal(auth, uuid: str, status_code: int) -> str:
    """Why DSpace refused X-On-Behalf-Of: <uuid> (looked up without the header)."""
    prefix = f"'login as' {uuid} refused (HTTP {status_code}): "
    if status_code == 401:
        return prefix + "log in as an administrator first"
    if status_code == 403:
        return prefix + "only administrators may act on behalf of other users"
    if status_code != 400:
        return prefix + "unexpected answer"
    from .authorizations import AuthorizationManager
    from .epersons import EPersonManager
    session = _session_of(auth)
    with session.on_behalf_of(None):
        try:
            person = EPersonManager(auth).get_eperson(uuid, all_languages=False)
        except NotFoundError:
            return prefix + f"there is no eperson {uuid}"
        except Exception as exc:                       # diagnosis is best effort
            logger.debug("eperson lookup for the 'login as' diagnosis failed: %s", exc)
            person = {"uuid": uuid}
        name = person.get("email") or uuid
        try:
            target_is_admin = AuthorizationManager(auth).is_site_admin(eperson=uuid)
        except Exception as exc:
            logger.debug("admin check for the 'login as' diagnosis failed: %s", exc)
            target_is_admin = False
    if target_is_admin:
        return prefix + (f"{name} is an administrator - DSpace does not allow 'login as' for administrators "
                         "(DSpace-CRIS: webui.user.assumelogin.admin = true)")
    return prefix + ("the feature is switched off on this server - set webui.user.assumelogin = true "
                     "in local.cfg and restart DSpace")


__all__ = ["ON_BEHALF_OF_HEADER", "on_behalf_of", "verify_on_behalf_of", "explain_refusal"]
