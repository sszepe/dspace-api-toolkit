"""Authorization checks: features of the current user and of another eperson"""

import pytest

from dspace_api.authorizations import Feature
from dspace_api.exceptions import AuthorizationError

from .helpers import unsupported_on


@pytest.mark.feature("authz.check")
def test_admin_checks(authorizations, new_item, collection, sandbox):
    item = new_item()
    assert authorizations.is_site_admin()
    assert authorizations.is_admin_of(collection)
    assert authorizations.is_admin_of(("community", sandbox["uuid"]))

    granted = authorizations.features_on(item)
    assert Feature.CAN_EDIT_METADATA in granted
    assert authorizations.check(item, [Feature.CAN_EDIT_METADATA, "noSuchFeature"]) == {
        Feature.CAN_EDIT_METADATA: True, "noSuchFeature": False}
    authorizations.require(item, Feature.CAN_EDIT_METADATA)
    assert authorizations.admin_scope()["site"] is True


@pytest.mark.feature("authz.check")
def test_checks_for_another_eperson(authorizations, new_item, new_eperson):
    item = new_item()
    person = new_eperson()
    assert authorizations.can(item, Feature.CAN_EDIT_METADATA, eperson=person) is False
    assert authorizations.is_site_admin(eperson=person) is False
    with pytest.raises(AuthorizationError):
        authorizations.require(item, Feature.CAN_DELETE, eperson=person)


@pytest.mark.feature("authz.batch")
def test_batch(authorizations, new_item):
    items = [new_item(), new_item()]
    result = authorizations.can_many(items, Feature.CAN_EDIT_METADATA)
    assert result == {i["uuid"]: True for i in items}
    many = authorizations.features_on_many(items)
    assert all(Feature.CAN_EDIT_METADATA in features for features in many.values())


@pytest.mark.feature("authz.features")
def test_features(authorizations):
    with unsupported_on(403, 404, what="feature listing"):
        ids = {f["id"] for f in authorizations.list_features()}
    assert {Feature.ADMINISTRATOR_OF, Feature.CAN_EDIT_METADATA} <= ids
    item_features = {f["id"] for f in authorizations.features_for_type("core.item")}
    assert Feature.WITHDRAW_ITEM in item_features
    assert authorizations.get_feature(Feature.CAN_DELETE)["id"] == Feature.CAN_DELETE
