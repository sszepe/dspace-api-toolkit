"""projection= / embed= request shaping"""

import pytest

from dspace_api.projection import get_embedded

from .helpers import unsupported_on, values


@pytest.mark.feature("projection.all_languages")
def test_all_languages_returns_every_language(items, new_item):
    item = new_item(metadata={"dc.title": [{"value": "Title EN", "language": "en"},
                                           {"value": "Titel DE", "language": "de"}]})
    with unsupported_on(400, what="projection=allLanguages"):
        full = items.get_item(item["uuid"], all_languages=True)
    assert sorted(values(full, "dc.title")) == sorted(["Title EN", "Titel DE"])


@pytest.mark.feature("projection.embed")
def test_embed_and_item_with_files(items, bundles, collection, new_item):
    item = new_item()
    bundle = bundles.get_or_create_bundle(item["uuid"])
    uploaded = bundles.upload_bitstream(bundle["uuid"], b"embedded", name="e.txt")
    fetched = items.get_item(item["uuid"], embed=["owningCollection", "bundles/bitstreams"])
    assert get_embedded(fetched, "owningCollection")["uuid"] == collection["uuid"]
    assert uploaded["uuid"] in [b["uuid"] for b in get_embedded(fetched, "bundles/bitstreams")]
    _, with_files = bundles.get_item_with_files(item["uuid"])
    assert uploaded["uuid"] in [b["uuid"] for bnd in with_files for b in bnd["bitstreams"]]


@pytest.mark.feature("projection.full")
def test_projection_full(items, new_item):
    item = new_item()
    with unsupported_on(400, what="projection=full"):
        full = items.get_item(item["uuid"], projection="full")
    assert full.get("_embedded"), "projection=full should embed linked resources"
