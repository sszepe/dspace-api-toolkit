"""'Login as' another user: X-On-Behalf-Of (needs webui.user.assumelogin = true, set in the test local.cfg)"""

import pytest

from dspace_api.authorizations import Feature
from dspace_api.exceptions import DSpaceError, OnBehalfOfError
from dspace_api.on_behalf import on_behalf_of

from .helpers import unsupported

UNKNOWN = "00000000-0000-4000-8000-000000000001"


@pytest.mark.feature("auth.on_behalf")
def test_requests_run_as_the_other_user(auth, epersons, authorizations, me, new_eperson, new_item):
    person, item = new_eperson(), new_item()
    try:
        with on_behalf_of(auth, person["email"]) as uuid:
            assert uuid == person["uuid"] and auth.session.acting_as == uuid
            assert epersons.get_current_user()["uuid"] == person["uuid"]
            assert authorizations.is_site_admin() is False
            assert authorizations.can(item, Feature.CAN_EDIT_METADATA) is False
            with on_behalf_of(auth, None):                        # nested: back to the admin
                assert epersons.get_current_user()["uuid"] == me["uuid"]
            assert epersons.get_current_user()["uuid"] == person["uuid"]
    except OnBehalfOfError as exc:
        if "switched off" in str(exc):
            unsupported(f"'login as' is off on this server ({exc})")
        raise
    assert auth.session.acting_as is None
    assert epersons.get_current_user()["uuid"] == me["uuid"]
    assert authorizations.can(item, Feature.CAN_EDIT_METADATA) is True


@pytest.mark.feature("auth.on_behalf")
def test_refusals_are_explained(auth, me):
    try:
        with on_behalf_of(auth, me["uuid"]):
            admin_allowed = True                                   # DSpace-CRIS: webui.user.assumelogin.admin
    except OnBehalfOfError as exc:
        admin_allowed = False
        assert "administrator" in str(exc) and exc.status_code == 400
    with pytest.raises(OnBehalfOfError, match="no eperson"):
        with on_behalf_of(auth, UNKNOWN):
            pass
    assert auth.session.acting_as is None
    if admin_allowed:
        pytest.skip("this server allows 'login as' for administrators (webui.user.assumelogin.admin)")


@pytest.mark.feature("auth.on_behalf")
def test_unverified_refusal_inside_the_block(auth, epersons):
    with on_behalf_of(auth, UNKNOWN, verify=False):
        with pytest.raises(DSpaceError) as info:
            epersons.get_current_user()
    assert info.value.status_code == 400
