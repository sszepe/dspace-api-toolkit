"""Discovery search configuration, filtered search, facets and browse indexes"""

import fnmatch

import pytest

from .helpers import eventually, unsupported


@pytest.fixture
def indexed_item(new_item, discovery, collection, run_id):
    """An item with a unique subject and author, once Solr has indexed it."""
    token = run_id.replace("-", "")
    subject, author = f"ITsubject{token}", f"Itauthor{token}, Test"
    item = new_item(metadata={"dc.subject": subject, "dc.contributor.author": author})
    eventually(lambda: discovery.count(f'"{subject}"', scope=collection["uuid"]) > 0, timeout=180,
               message="new item not indexed")
    return {"item": item, "subject": subject, "author": author}


@pytest.mark.feature("discovery.config")
def test_search_config(discovery):
    config = discovery.get_search_config()
    assert config.get("filters") and config.get("sortOptions") is not None
    names = discovery.get_search_filters()
    assert {"title", "author"} & set(names), names
    operators = {o.get("operator") for f in config["filters"] for o in f.get("operators") or []}
    assert "equals" in operators


@pytest.mark.feature("discovery.filters")
def test_filtered_search(discovery, collection, indexed_item):
    filters = {"subject": indexed_item["subject"]}
    if "subject" not in discovery.get_search_filters():
        unsupported("no 'subject' search filter in the default discovery configuration")
    found = list(discovery.iter_search(dso_type="item", scope=collection["uuid"], filters=filters, page_size=5))
    assert [o["uuid"] for o in found] == [indexed_item["item"]["uuid"]]
    assert discovery.count(dso_type="item", scope=collection["uuid"], filters=filters) == 1
    none = {"subject": (indexed_item["subject"], "notequals")}
    assert indexed_item["item"]["uuid"] not in [
        o["uuid"] for o in discovery.iter_search(dso_type="item", scope=collection["uuid"], filters=none)]


@pytest.mark.feature("discovery.facets")
def test_facets(discovery, collection, indexed_item):
    names = [f["name"] for f in discovery.list_facets()]
    if "subject" not in names:
        unsupported(f"no 'subject' facet ({names})")
    facets = discovery.search_facets(scope=collection["uuid"], names=["subject"])
    assert facets and facets[0]["name"] == "subject"
    counts = discovery.facet_counts("subject", scope=collection["uuid"], page_size=2)
    assert counts.get(indexed_item["subject"].lower()) == 1 or counts.get(indexed_item["subject"]) == 1, counts
    assert discovery.get_facets(scope=collection["uuid"], facet_fields=["subject"])["_embedded"]["facets"]


@pytest.mark.feature("browse.indexes")
def test_browse_indexes(discovery):
    indexes = discovery.get_browse_indexes()
    assert indexes
    first = indexes[0]
    assert discovery.get_browse_index(first["id"])["id"] == first["id"]
    author_index = discovery.find_browse_index("dc.contributor.author")
    if author_index is None:
        unsupported("no browse index configured for dc.contributor.author")
    # the index may be configured with a wildcard, e.g. ["dc.contributor.*", "dc.creator"]
    assert any(fnmatch.fnmatch("dc.contributor.author", pattern) for pattern in author_index.get("metadata") or []), \
        author_index.get("metadata")


@pytest.mark.feature("browse.entries")
def test_browse_entries_and_items(discovery, collection, indexed_item):
    index = discovery.find_browse_index("dc.contributor.author")
    if index is None or index.get("browseType") != "valueList":
        unsupported("no metadata browse (valueList) for dc.contributor.author")

    wanted = indexed_item["author"].casefold()
    seen = []

    def entry():
        seen[:] = list(discovery.iter_browse_entries(index["id"], scope=collection["uuid"], max_results=200))
        return next((e for e in seen if str(e.get("value", "")).casefold() == wanted), None)

    try:
        found = eventually(entry, timeout=180, message="author not in browse index")
    except AssertionError as exc:
        values = [e.get("value") for e in seen][:20]
        raise AssertionError(f"{exc}: index {index['id']!r} (scope {collection['uuid']}) lists {values}") from None
    assert found.get("count") == 1
    by_value = [i["uuid"] for i in discovery.iter_browse_items(index["id"], value=indexed_item["author"],
                                                                scope=collection["uuid"])]
    assert by_value == [indexed_item["item"]["uuid"]]
    assert [i["uuid"] for i in discovery.iter_entry_items(found)] == [indexed_item["item"]["uuid"]]

    flat = next((i for i in discovery.get_browse_indexes() if i.get("browseType") == "flatBrowse"), None)
    if flat:
        items = [i["uuid"] for i in discovery.iter_browse_items(flat["id"], scope=collection["uuid"])]
        assert indexed_item["item"]["uuid"] in items
