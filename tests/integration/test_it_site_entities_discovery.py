"""Site / Terms of Use, entity types, discovery"""

import pytest

from .helpers import eventually


@pytest.mark.feature("site.terms_of_use")
def test_read_site_and_terms(site):
    assert site.get_site() is not None
    terms_values = site.get_terms_of_use_values()
    assert isinstance(terms_values, list)
    text = site.get_terms_of_use("en")
    assert text is None or isinstance(text, str)


@pytest.mark.feature("entity_types.list")
def test_entity_types(entity_types, entity_type):
    types = entity_types.get_entity_types()
    assert isinstance(types, list)
    if entity_type:
        assert entity_type in {t["label"] for t in types}
        # the sandbox collection is of this type and we are admin
        assert entity_types.can_submit(entity_type)


@pytest.mark.feature("discovery.search")
def test_new_item_is_found(discovery, new_item, run_id):
    token = f"zzit{run_id.replace('-', '')}"
    item = new_item(title=f"Discovery {token}")

    def found():
        results = discovery.get_all_results(token, max_results=20)
        return any(r.get("uuid") == item["uuid"] for r in results)
    eventually(found, timeout=180, message="new item not found in discovery")
