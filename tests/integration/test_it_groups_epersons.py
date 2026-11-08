"""Groups and epersons"""

import pytest


@pytest.mark.feature("epersons.crud")
def test_eperson_lifecycle(epersons, run_id):
    email = f"it-{run_id}-crud@example.org"
    person = epersons.create_eperson(email, first_name="Ada", last_name="Lovelace", language="en")
    try:
        assert epersons.find_by_email(email)["uuid"] == person["uuid"]
        assert epersons.resolve_eperson(email) == person["uuid"]
        assert epersons.set_can_log_in(person["uuid"], False)["canLogIn"] is False
        epersons.upsert_eperson_metadata(person["uuid"], {"eperson.phone": "+43 1 234"})
        assert epersons.get_eperson(person["uuid"])["metadata"]["eperson.phone"][0]["value"] == "+43 1 234"
    finally:
        assert epersons.delete_eperson(person["uuid"]) is True
    assert epersons.find_by_email(email) is None


@pytest.mark.feature("epersons.agreement")
def test_end_user_agreement(epersons, new_eperson):
    person = new_eperson()
    assert not epersons.has_accepted_end_user_agreement(person["uuid"])
    epersons.accept_end_user_agreement(person["uuid"])
    assert epersons.has_accepted_end_user_agreement(person["uuid"])
    epersons.accept_end_user_agreement(person["uuid"])                          # idempotent
    values = epersons.get_eperson(person["uuid"])["metadata"]["dspace.agreements.end-user"]
    assert len(values) == 1
    epersons.revoke_end_user_agreement(person["uuid"])
    assert not epersons.has_accepted_end_user_agreement(person["uuid"])

    epersons.ignore_end_user_agreement(person["uuid"])                          # service account
    assert epersons.is_ignoring_end_user_agreement(person["uuid"])
    epersons.ignore_end_user_agreement(person["uuid"], ignore=False)
    assert not epersons.is_ignoring_end_user_agreement(person["uuid"])


@pytest.mark.feature("groups.crud")
def test_group_lifecycle(groups, run_id):
    group = groups.create_group(f"IT crud {run_id}", description="integration test")
    try:
        assert groups.find_group_by_name(f"IT crud {run_id}")["uuid"] == group["uuid"]
        renamed = groups.rename_group(group["uuid"], f"IT crud {run_id} renamed")
        assert renamed["name"] == f"IT crud {run_id} renamed"
    finally:
        assert groups.delete_group(group["uuid"]) is True


@pytest.mark.feature("groups.members")
def test_group_members(groups, new_group, new_eperson):
    group, sub = new_group(), new_group()
    person = new_eperson()
    added = groups.add_members(group["uuid"], epersons=[person["email"]], subgroups=[sub["uuid"]])
    assert added == {"added_epersons": [person["uuid"]], "added_subgroups": [sub["uuid"]]}
    assert [e["uuid"] for e in groups.get_group_epersons(group["uuid"])] == [person["uuid"]]
    assert [g["uuid"] for g in groups.get_subgroups(group["uuid"])] == [sub["uuid"]]
    assert groups.add_members(group["uuid"], epersons=[person["uuid"]]) == {"added_epersons": [],
                                                                            "added_subgroups": []}
    result = groups.sync_members(group["uuid"], epersons=[], subgroups=[])
    assert result["removed_epersons"] == [person["uuid"]] and result["removed_subgroups"] == [sub["uuid"]]
    assert groups.get_group_epersons(group["uuid"]) == [] and groups.get_subgroups(group["uuid"]) == []
