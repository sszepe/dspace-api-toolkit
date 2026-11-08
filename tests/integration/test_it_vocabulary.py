"""Controlled vocabularies and hierarchical vocabularies"""

import pytest

from dspace_api.exceptions import DSpaceError

from .helpers import unsupported, unsupported_on


@pytest.mark.feature("vocabulary.lookup")
def test_lookup_and_validation(vocabulary):
    vocabularies = vocabulary.get_vocabularies()
    assert vocabularies
    scrollable = next((v for v in vocabularies if v.get("scrollable") and not v.get("hierarchical")), None)
    if scrollable is None:
        unsupported("no scrollable value-pairs vocabulary configured")
    name = scrollable["id"]
    assert vocabulary.get_vocabulary(name)["id"] == name
    entries = vocabulary.get_entries(name, max_results=5)
    assert entries
    value = entries[0]["value"]
    assert vocabulary.lookup(name, value)["value"] == value
    assert vocabulary.invalid_values(name, [value, "toolkit-it-no-such-value"]) == ["toolkit-it-no-such-value"]
    assert vocabulary.to_metadata_value(entries[0])["value"] == value


@pytest.mark.feature("vocabulary.form_field")
def test_vocabulary_for_form_field(vocabulary, collection):
    for field in ("dc.type", "dc.language.iso", "dc.subject"):
        with unsupported_on(400, 422, what="byMetadataAndCollection"):
            found = vocabulary.vocabulary_for_field(field, collection["uuid"])
        if found:
            assert found.get("id")
            return
    unsupported("no vocabulary configured for dc.type, dc.language.iso or dc.subject in this collection")


@pytest.mark.feature("vocabulary.hierarchy")
def test_hierarchical_vocabulary(vocabulary):
    hierarchical = next((v for v in vocabulary.get_vocabularies() if v.get("hierarchical")), None)
    if hierarchical is None:
        unsupported("no hierarchical vocabulary configured")
    name = hierarchical["id"]
    try:
        top = vocabulary.get_top_entries(name)
    except DSpaceError as exc:
        pytest.fail(f"top entries of {name}: {exc}")
    assert top
    walked = []
    for depth, entry in vocabulary.iter_tree(name, max_depth=1):
        walked.append((depth, entry))
        if len(walked) >= 30:
            break
    child = next((e for d, e in walked if d == 1), None)
    if child:
        path = vocabulary.get_path(name, child["id"])
        assert path[-1]["id"] == child["id"] and len(path) >= 2
        assert vocabulary.get_parent(name, child["id"])["id"] == path[-2]["id"]
    assert vocabulary.get_entry_detail(name, top[0]["id"])["id"] == top[0]["id"]
