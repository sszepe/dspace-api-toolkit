"""
Tests for entity type lookups and submission gating (entity_types.py)
"""

import pytest
import responses

from dspace_api.entity_types import EntityTypeManager


@pytest.fixture
def mgr(http_auth):
    return EntityTypeManager(http_auth)


TYPES = [{"id": 1, "label": "Publication"}, {"id": 2, "label": "Person"}]


class TestEntityTypes:
    def test_list_and_by_label(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/entitytypes"), json=hal_page("entitytypes", TYPES))
        http.add(responses.GET, api_url("/core/entitytypes/label/Person"), json=TYPES[1])
        assert len(mgr.get_entity_types()) == 2
        assert mgr.get_entity_type_by_label("Person")["id"] == 2

    def test_authorized_searches_and_gating(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/entitytypes/search/findAllByAuthorizedCollection"),
                 json=hal_page("entitytypes", TYPES))
        http.add(responses.GET, api_url("/core/entitytypes/search/findAllByAuthorizedExternalSource"),
                 json=hal_page("entitytypes", TYPES[:1]))
        assert mgr.authorized_labels() == {"Publication", "Person"}
        assert mgr.can_submit("Person") is True
        assert mgr.can_submit("Project") is False
        assert [t["label"] for t in mgr.get_authorized_external_source_entity_types()] == ["Publication"]

    def test_submit_collections(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/collections/search/findSubmitAuthorizedByEntityType"),
                 json=hal_page("collections", [{"uuid": "c1"}, {"uuid": "c2"}]))
        assert len(mgr.get_submit_collections("Person", query="music", max_results=1)) == 1
        assert http.calls[-1].request.params["entityType"] == "Person"
        assert http.calls[-1].request.params["query"] == "music"
        mgr.get_submit_collections("Person")
        assert "query" not in http.calls[-1].request.params
