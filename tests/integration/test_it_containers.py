"""Community and collection extras: logos, hierarchy, template, license, mapping, harvesting"""

import base64

import pytest

from dspace_api.communities import build_dso_metadata

from .helpers import unsupported_on

# 1x1 transparent PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


@pytest.mark.feature("containers.logo")
@pytest.mark.parametrize("kind", ["community", "collection"])
def test_logo(kind, communities, collections, sandbox, new_collection):
    mgr, uuid = (communities, sandbox["uuid"]) if kind == "community" else (collections, new_collection()["uuid"])
    assert mgr.get_logo(uuid) is None
    first = mgr.set_logo(uuid, PNG, name="logo.png")
    assert mgr.get_logo(uuid)["uuid"] == first["uuid"]
    second = mgr.set_logo(uuid, PNG, name="logo2.png")
    assert second["uuid"] != first["uuid"] and mgr.get_logo(uuid)["uuid"] == second["uuid"]
    assert mgr.delete_logo(uuid) is True
    assert mgr.get_logo(uuid) is None


@pytest.mark.feature("containers.hierarchy")
def test_hierarchy(communities, collections, sandbox, collection, run_id, cleanup):
    sub = communities.create_community(build_dso_metadata(f"IT sub {run_id}"), parent_uuid=sandbox["uuid"])
    cleanup(lambda: communities.delete_community(sub["uuid"]))
    assert communities.get_parent_community(sub["uuid"])["uuid"] == sandbox["uuid"]
    assert collections.get_parent_community(collection["uuid"])["uuid"] == sandbox["uuid"]
    assert [c["uuid"] for c in communities.get_ancestors(sub["uuid"])][-1] == sandbox["uuid"]

    tree = [(d, k, o["uuid"]) for d, k, o in communities.iter_tree(sandbox["uuid"])]
    assert (0, "community", sandbox["uuid"]) == tree[0]
    assert (1, "collection", collection["uuid"]) in tree and (1, "community", sub["uuid"]) in tree

    title = sandbox["metadata"]["dc.title"][0]["value"]
    for search in (communities.find_admin_authorized, communities.find_edit_authorized,
                   communities.find_add_authorized):
        with unsupported_on(400, 404, what=search.__name__):
            found = search(title)
        assert sandbox["uuid"] in [c["uuid"] for c in found], search.__name__
    with unsupported_on(400, 404, what="collections findAdminAuthorized"):
        found = collections.find_admin_authorized(collection["name"])
    assert collection["uuid"] in [c["uuid"] for c in found]


@pytest.mark.feature("collections.item_template")
def test_item_template(collections, new_collection):
    coll = new_collection()
    assert collections.get_item_template(coll["uuid"]) is None
    template = collections.set_item_template(coll["uuid"], {"dc.type": "Journal Article"})
    assert template["metadata"]["dc.type"][0]["value"] == "Journal Article"
    collections.set_item_template(coll["uuid"], {"dc.type": "Thesis", "dc.language.iso": "en"})
    md = collections.get_item_template(coll["uuid"])["metadata"]
    assert md["dc.type"][0]["value"] == "Thesis" and md["dc.language.iso"][0]["value"] == "en"
    assert collections.delete_item_template(coll["uuid"]) is True
    assert collections.get_item_template(coll["uuid"]) is None


@pytest.mark.feature("collections.license")
def test_license(collections, new_collection):
    coll = new_collection()
    default = collections.get_license(coll["uuid"])
    assert default["custom"] is False and default["text"]
    collections.set_license(coll["uuid"], "IT custom license")
    custom = collections.get_license(coll["uuid"])
    assert custom["custom"] is True and custom["text"] == "IT custom license"
    collections.set_license(coll["uuid"], None)
    assert collections.get_license(coll["uuid"])["custom"] is False


@pytest.mark.feature("collections.mapping")
def test_mapping(collections, new_collection, new_item):
    item = new_item()
    target = new_collection()
    assert collections.map_item(target["uuid"], item["uuid"])
    assert item["uuid"] in [i["uuid"] for i in collections.get_mapped_items(target["uuid"])]
    assert collections.unmap_item(target["uuid"], item["uuid"])
    assert item["uuid"] not in [i["uuid"] for i in collections.get_mapped_items(target["uuid"])]


@pytest.mark.feature("collections.harvester")
def test_harvester(collections, new_collection):
    coll = new_collection()
    with unsupported_on(404, what="harvester metadata configs"):
        configs = collections.get_harvester_metadata_configs()
    assert "dc" in [c["id"] for c in configs]
    assert collections.get_harvester(coll["uuid"])["harvest_type"] in (None, "NONE")
    assert collections.disable_harvesting(coll["uuid"])["harvest_type"] in (None, "NONE")


@pytest.mark.feature("collections.default_access")
def test_default_access_conditions(collections, new_collection):
    coll = new_collection()
    policies = collections.get_default_access_conditions(coll["uuid"])
    assert any(p.get("action") == "DEFAULT_BITSTREAM_READ" for p in policies)
