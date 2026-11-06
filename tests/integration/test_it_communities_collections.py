"""Communities, collections and their role groups"""

import pytest

from dspace_api.communities import build_dso_metadata
from dspace_api.permissions import PolicyTypes

from .helpers import eventually


@pytest.mark.feature("communities.crud")
def test_subcommunity_with_admins(communities, sandbox, new_group, run_id):
    admins = new_group()
    sub = communities.create_community(build_dso_metadata(f"IT sub {run_id}", description="<p>x</p>"),
                                       parent_uuid=sandbox["uuid"])
    try:
        assert sub["uuid"] in [c["uuid"] for c in communities.get_subcommunities(sandbox["uuid"])]
        assert communities.get_admin_group(sub["uuid"]) is None
        communities.add_admins(sub["uuid"], subgroups=[admins["uuid"]])
        assert [g["uuid"] for g in communities.get_admins(sub["uuid"])["subgroups"]] == [admins["uuid"]]
    finally:
        assert communities.delete_community(sub["uuid"]) is True


@pytest.mark.feature("collections.roles")
def test_collection_admin_and_submitter_groups(collections, new_collection, new_group, new_eperson):
    coll, staff, person = new_collection(), new_group(), new_eperson()
    collections.add_admins(coll["uuid"], subgroups=[staff["uuid"]])
    collections.add_submitters(coll["uuid"], epersons=[person["uuid"]])
    assert [g["uuid"] for g in collections.get_role_members(coll["uuid"], "adminGroup")["subgroups"]] == [staff["uuid"]]
    assert [e["uuid"] for e in collections.get_role_members(coll["uuid"], "submittersGroup")["epersons"]] == \
        [person["uuid"]]


@pytest.mark.feature("collections.read_restriction")
def test_restrict_default_read(collections, new_collection, new_group):
    coll, readers = new_collection(), new_group()
    assert collections.is_read_restricted(coll["uuid"]) == {"items": False, "bitstreams": False}
    collections.restrict_item_read(coll["uuid"], subgroups=[readers["uuid"]])
    collections.restrict_bitstream_read(coll["uuid"], subgroups=[readers["uuid"]])
    assert collections.is_read_restricted(coll["uuid"]) == {"items": True, "bitstreams": True}


@pytest.mark.feature("collections.submission_policies")
def test_submission_policies(collections, new_collection, new_group):
    coll, group = new_collection(), new_group()
    policy = collections.add_submission_policy(coll["uuid"], group["uuid"], name="IT submit")
    assert policy["policyType"] == PolicyTypes.SUBMISSION
    assert policy["id"] in [p["id"] for p in collections.get_submission_policies(coll["uuid"])]
    assert collections.delete_submission_policy(policy["id"]) is True


@pytest.mark.feature("collections.items")
def test_collection_items(collections, new_collection, items, entity_type, run_id, cleanup):
    coll = new_collection()
    created = []
    for n in range(3):
        item = items.create_item(coll["uuid"], {"dc.title": f"IT coll item {run_id}-{n}", "dc.date.issued": "2026"},
                                 entity_type)
        cleanup(lambda uuid=item["uuid"]: items.delete_item(uuid))
        created.append(item["uuid"])
    eventually(lambda: collections.count_collection_items(coll["uuid"]) == 3, timeout=180,
               message="items not indexed in the collection")
    page = collections.get_collection_items(coll["uuid"], page=0, size=2, sort="dc.title,ASC")
    assert len(page["_embedded"]["items"]) == 2
    assert page["page"]["totalElements"] == 3 and page["page"]["totalPages"] == 2
    assert sorted(i["uuid"] for i in collections.iter_collection_items(coll["uuid"], page_size=2)) == sorted(created)
