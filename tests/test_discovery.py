"""
Tests for DSpace discovery module
"""

import pytest
from unittest.mock import Mock
from dspace_api.discovery import DiscoveryManager
from dspace_api.exceptions import handle_response_error

class TestDiscoveryManager:
    """Test cases for DiscoveryManager"""

    def test_init(self, mock_auth):
        """Test DiscoveryManager initialization"""
        manager = DiscoveryManager(mock_auth)

        assert manager.auth == mock_auth
        assert manager.session == mock_auth.session
        assert manager.config == mock_auth.config

    def test_search_objects_basic(self, mock_auth, mock_responses, api_helpers):
        """Test basic search objects functionality"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.search_objects("test query")

        assert result == mock_responses.discovery_search_results

        # Verify API call was made with default parameters
        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert params["query"] == "test query"
        assert params["sort"] == "score,DESC"
        assert params["page"] == 0
        assert params["size"] == 10
        assert params["projection"] == "preventMetadataSecurity"

    def test_search_objects_with_all_params(self, mock_auth, mock_responses, api_helpers):
        """Test search objects with all parameters"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.search_objects(
            query="dc.title:test",
            page=2,
            size=20,
            sort="dc.title,ASC",
            projection="full",
            scope="collection-uuid",
            filters=["entityType:Publication", "dc.subject:science"]
        )

        # Verify all parameters were passed
        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert params["query"] == "dc.title:test"
        assert params["page"] == 2
        assert params["size"] == 20
        assert params["sort"] == "dc.title,ASC"
        assert params["projection"] == "full"
        assert params["scope"] == "collection-uuid"
        assert params["f.0"] == "entityType:Publication"
        assert params["f.1"] == "dc.subject:science"

    def test_search_objects_error(self, mock_auth, api_helpers):
        """Test search objects with error response"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(manager.session, "get", 500)

        with pytest.raises(Exception):  # Should raise server error
            manager.search_objects("test query")

    def test_find_by_metadata_exact_match(self, mock_auth, mock_responses, api_helpers):
        """Test find by metadata with exact match"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.find_by_metadata("dc.title", "Test Publication", exact_match=True)

        # Verify query construction for exact match
        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert params["query"] == 'dc.title:"Test Publication"'

    def test_find_by_metadata_partial_match(self, mock_auth, mock_responses, api_helpers):
        """Test find by metadata with partial match"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.find_by_metadata("dc.creator", "Smith", exact_match=False)

        # Verify query construction for partial match
        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert params["query"] == 'dc.creator:Smith'

    def test_find_by_metadata_with_entity_type(self, mock_auth, mock_responses, api_helpers):
        """Test find by metadata with entity type filter"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.find_by_metadata("dc.title", "Test", entity_type="Publication")

        # Verify query includes entity type
        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert 'entityType:"Publication"' in params["query"]
        assert 'dc.title:"Test"' in params["query"]

    def test_find_by_title(self, mock_auth, mock_responses, api_helpers):
        """Test find by title convenience method"""
        manager = DiscoveryManager(mock_auth)

        # Mock the find_by_metadata method
        manager.find_by_metadata = Mock(return_value=mock_responses.discovery_search_results)

        result = manager.find_by_title("Test Title", entity_type="Publication", exact_match=True)

        # Verify it calls find_by_metadata with correct parameters
        manager.find_by_metadata.assert_called_once_with(
            "dc.title", "Test Title", "Publication", True
        )

    def test_find_by_author(self, mock_auth, mock_responses):
        """Test find by author convenience method"""
        manager = DiscoveryManager(mock_auth)

        manager.find_by_metadata = Mock(return_value=mock_responses.discovery_search_results)

        result = manager.find_by_author("Smith, John", entity_type="Publication", exact_match=False)

        manager.find_by_metadata.assert_called_once_with(
            "dc.contributor.author", "Smith, John", "Publication", False
        )

    def test_find_persons(self, mock_auth, mock_responses, api_helpers):
        """Test find persons method"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.find_persons("Beethoven", exact_match=True)

        # Verify complex query for person search
        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        query = params["query"]

        assert 'person.familyName:"Beethoven"' in query
        assert 'person.givenName:"Beethoven"' in query
        assert 'dc.title:"Beethoven"' in query
        assert 'entityType:"Person"' in query

    def test_find_persons_partial_match(self, mock_auth, mock_responses, api_helpers):
        """Test find persons with partial match"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.find_persons("Bach", exact_match=False)

        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        query = params["query"]

        # Should not have quotes for partial match
        assert 'person.familyName:Bach' in query
        assert 'person.givenName:Bach' in query

    def test_find_by_handle(self, mock_auth, mock_responses, api_helpers):
        """Test find by handle method"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.find_by_handle("123456789/123")

        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert params["query"] == 'handle:"123456789/123"'
        assert params["size"] == 1

    def test_find_by_uuid(self, mock_auth, mock_responses, api_helpers):
        """Test find by UUID method"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.find_by_uuid("12345678-1234-1234-1234-123456789012")

        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert params["query"] == 'uuid:"12345678-1234-1234-1234-123456789012"'
        assert params["size"] == 1

    def test_find_recent_items(self, mock_auth, mock_responses, api_helpers):
        """Test find recent items method"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.find_recent_items(entity_type="Publication", days=30, size=15)

        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert 'entityType:"Publication"' in params["query"]
        assert params["sort"] == "lastModified,DESC"
        assert params["size"] == 15

    def test_search_in_collection(self, mock_auth, mock_responses, api_helpers):
        """Test search within collection"""
        manager = DiscoveryManager(mock_auth)

        # Mock the search_objects method
        manager.search_objects = Mock(return_value=mock_responses.discovery_search_results)

        result = manager.search_in_collection("collection-uuid", "test query", size=20)

        manager.search_objects.assert_called_once_with(
            "test query", scope="collection-uuid", size=20
        )

    def test_search_in_community(self, mock_auth, mock_responses):
        """Test search within community"""
        manager = DiscoveryManager(mock_auth)

        manager.search_objects = Mock(return_value=mock_responses.discovery_search_results)

        result = manager.search_in_community("community-uuid", "*:*", page=1)

        manager.search_objects.assert_called_once_with(
            "*:*", scope="community-uuid", page=1
        )

    def test_parse_search_results(self, mock_auth, mock_responses):
        """Test parsing of search results"""
        manager = DiscoveryManager(mock_auth)

        parsed = manager.parse_search_results(mock_responses.discovery_search_results)

        assert len(parsed) == 2

        # Check first result (Publication)
        pub_item = parsed[0]
        assert pub_item["uuid"] == "discovered-item-1"
        assert pub_item["title"] == "Discovered Publication 1"
        assert pub_item["author"] == "Discovery Author"
        assert pub_item["entityType"] == "Publication"
        assert "testing" in pub_item["metadata"]["dc.subject"]

        # Check second result (Person)
        person_item = parsed[1]
        assert person_item["uuid"] == "discovered-person-1"
        assert person_item["entityType"] == "Person"
        assert person_item["person_name"] == "Beethoven, Ludwig van"

    def test_parse_search_results_empty(self, mock_auth):
        """Test parsing empty search results"""
        manager = DiscoveryManager(mock_auth)

        empty_results = {
            "_embedded": {"searchResult": {"_embedded": {"objects": []}}},
            "page": {"totalElements": 0}
        }

        parsed = manager.parse_search_results(empty_results)

        assert len(parsed) == 0

    def test_parse_search_results_missing_metadata(self, mock_auth):
        """Test parsing results with missing metadata fields"""
        manager = DiscoveryManager(mock_auth)

        results_with_missing_metadata = {
            "_embedded": {
                "searchResult": {
                    "_embedded": {
                        "objects": [{
                            "_embedded": {
                                "indexableObject": {
                                    "uuid": "test-item",
                                    "name": "Test Item",
                                    "type": "item",
                                    "metadata": {}  # Empty metadata
                                }
                            }
                        }]
                    }
                }
            }
        }

        parsed = manager.parse_search_results(results_with_missing_metadata)

        assert len(parsed) == 1
        assert parsed[0]["uuid"] == "test-item"
        assert parsed[0]["metadata"] == {}
        assert "title" not in parsed[0]  # Should not have title if not in metadata

    def test_count_results(self, mock_auth, mock_responses, api_helpers):
        """Test count results method"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        count = manager.count_results("test query")

        assert count == 2  # From mock response totalElements

        # Verify it requested only 1 result for counting
        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert params["size"] == 1

    def test_get_all_results_max_limit(self, mock_auth, mock_responses):
        """Test get all results respects max limit"""
        manager = DiscoveryManager(mock_auth)

        # Mock to return more results than max_results
        large_results = {
            "_embedded": {
                "searchResult": {
                    "_embedded": {
                        "objects": [
                            {"_embedded": {"indexableObject": {"uuid": f"item-{i}", "metadata": {}}}}
                            for i in range(10)
                        ]
                    }
                }
            },
            "page": {"totalPages": 5, "number": 0}
        }

        manager.search_objects = Mock(return_value=large_results)

        all_results = manager.get_all_results("test query", max_results=5)

        assert len(all_results) == 5  # Should be limited to max_results

    def test_get_facets_uses_facet_endpoint(self, mock_auth):
        """get_facets returns the facets of /discover/search/facets, limited to facet_fields"""
        manager = DiscoveryManager(mock_auth)
        manager.search_facets = Mock(return_value=[{"name": "subject"}])
        result = manager.get_facets("test query", ["subject", "author"], scope="s")
        assert result == {"_embedded": {"facets": [{"name": "subject"}]}}
        manager.search_facets.assert_called_once_with(query="test query", scope="s", configuration=None,
                                                      filters=None, names=["subject", "author"])

    def test_exception_handling_in_search(self, mock_auth):
        """Test exception handling in search methods"""
        manager = DiscoveryManager(mock_auth)

        import requests
        manager.session.get = Mock(side_effect=requests.exceptions.ConnectionError("Network error"))

        with pytest.raises(requests.exceptions.ConnectionError):
            manager.search_objects("test query")

    def test_parse_results_with_person_name_construction(self, mock_auth):
        """Test parsing results correctly constructs person names"""
        manager = DiscoveryManager(mock_auth)

        person_results = {
            "_embedded": {
                "searchResult": {
                    "_embedded": {
                        "objects": [
                            {
                                "_embedded": {
                                    "indexableObject": {
                                        "uuid": "person-1",
                                        "entityType": "Person",
                                        "metadata": {
                                            "person.familyName": [{"value": "Mozart"}],
                                            "person.givenName": [{"value": "Wolfgang Amadeus"}]
                                        }
                                    }
                                }
                            },
                            {
                                "_embedded": {
                                    "indexableObject": {
                                        "uuid": "person-2",
                                        "entityType": "Person",
                                        "metadata": {
                                            "person.familyName": [{"value": "Bach"}]
                                            # No given name
                                        }
                                    }
                                }
                            }
                        ]
                    }
                }
            }
        }

        parsed = manager.parse_search_results(person_results)

        assert len(parsed) == 2
        assert parsed[0]["person_name"] == "Mozart, Wolfgang Amadeus"
        assert parsed[1]["person_name"] == "Bach"  # Should handle missing given name

    def test_parse_results_metadata_extraction(self, mock_auth):
        """Test that parse results correctly extracts common metadata fields"""
        manager = DiscoveryManager(mock_auth)

        complex_results = {
            "_embedded": {
                "searchResult": {
                    "_embedded": {
                        "objects": [
                            {
                                "_embedded": {
                                    "indexableObject": {
                                        "uuid": "complex-item",
                                        "metadata": {
                                            "dc.title": [{"value": "Complex Title"}],
                                            "dc.contributor.author": [
                                                {"value": "Author One"},
                                                {"value": "Author Two"}
                                            ],
                                            "dc.date.issued": [{"value": "2025"}],
                                            "dc.subject": [
                                                {"value": "subject1"},
                                                {"value": "subject2"}
                                            ],
                                            "dc.description": [{"value": "Description here"}],
                                            "dc.identifier.uri": [{"value": "http://example.com"}],
                                            "custom.field": [{"value": "Not extracted"}]
                                        }
                                    }
                                }
                            }
                        ]
                    }
                }
            }
        }

        parsed = manager.parse_search_results(complex_results)

        item = parsed[0]
        assert item["title"] == "Complex Title"
        assert item["author"] == "Author One"  # First author
        assert len(item["metadata"]["dc.contributor.author"]) == 2
        assert len(item["metadata"]["dc.subject"]) == 2
        assert "dc.date.issued" in item["metadata"]
        assert "dc.description" in item["metadata"]
        assert "custom.field" not in item["metadata"]  # Not in common fields

    def test_find_recent_items_without_entity_type(self, mock_auth, mock_responses, api_helpers):
        """Test find recent items without entity type filter"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        result = manager.find_recent_items(days=7, size=5)

        call_args = manager.session.get.call_args
        params = call_args[1]["params"]
        assert params["query"] == "*:*"  # Should not include entity type filter
        assert params["size"] == 5

    def test_search_with_filters_parameter(self, mock_auth, mock_responses, api_helpers):
        """Test search objects with filters parameter"""
        manager = DiscoveryManager(mock_auth)

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=mock_responses.discovery_search_results
        )

        filters = [
            "entityType:Publication",
            "dc.date.issued:[2020 TO 2025]",
            "dc.subject:science"
        ]

        result = manager.search_objects("test", filters=filters)

        call_args = manager.session.get.call_args
        params = call_args[1]["params"]

        # Verify filter parameters are correctly set
        assert params["f.0"] == "entityType:Publication"
        assert params["f.1"] == "dc.date.issued:[2020 TO 2025]"
        assert params["f.2"] == "dc.subject:science"

    def test_get_all_results_empty_response(self, mock_auth):
        """Test get all results with empty initial response"""
        manager = DiscoveryManager(mock_auth)

        empty_results = {
            "_embedded": {"searchResult": {"_embedded": {"objects": []}}},
            "page": {"totalPages": 0}
        }

        manager.search_objects = Mock(return_value=empty_results)

        all_results = manager.get_all_results("no results query")

        assert len(all_results) == 0
        assert manager.search_objects.call_count == 1  # Should only call once

    def test_count_results_with_zero_results(self, mock_auth, api_helpers):
        """Test count results when no results found"""
        manager = DiscoveryManager(mock_auth)

        empty_results = {
            "page": {"totalElements": 0}
        }

        api_helpers.mock_api_response(
            manager.session,
            "get",
            200,
            json_data=empty_results
        )

        count = manager.count_results("no results query")

        assert count == 0

    def test_search_objects_request_timeout(self, mock_auth):
        """Test search objects with request timeout"""
        manager = DiscoveryManager(mock_auth)

        import requests
        manager.session.get = Mock(side_effect=requests.exceptions.Timeout("Request timeout"))

        with pytest.raises(requests.exceptions.Timeout):
            manager.search_objects("test query")

    def test_get_all_results_stops_on_last_page(self, mock_auth):
        mgr = DiscoveryManager(mock_auth)

        # Page 0 of 2 (has 2 items)
        page0 = {
            "_embedded": {
                "searchResult": {
                    "_embedded": {
                        "objects": [
                            {"_embedded": {"indexableObject": {"uuid": "p0-1"}}},
                            {"_embedded": {"indexableObject": {"uuid": "p0-2"}}},
                        ]
                    },
                    "page": {"number": 0, "totalPages": 2},
                }
            }
        }
        # Page 1 of 2 (has 1 item) → should trigger the "last page" break
        page1 = {
            "_embedded": {
                "searchResult": {
                    "_embedded": {
                        "objects": [
                            {"_embedded": {"indexableObject": {"uuid": "p1-1"}}},
                        ]
                    },
                    "page": {"number": 1, "totalPages": 2},
                }
            }
        }

        mgr.search_objects = Mock(side_effect=[page0, page1])

        results = mgr.get_all_results("query", size=2)

        # Collected all 3 items and made exactly 2 requests
        assert [r["uuid"] for r in results] == ["p0-1", "p0-2", "p1-1"]
        assert mgr.search_objects.call_count == 2


    def test_get_facets_without_facet_fields(self, mock_auth):
        """Without facet_fields every facet is returned"""
        manager = DiscoveryManager(mock_auth)
        manager.search_facets = Mock(return_value=[{"name": "a"}, {"name": "b"}])
        assert len(manager.get_facets("title:x")["_embedded"]["facets"]) == 2
        assert manager.search_facets.call_args.kwargs["names"] is None

    def test_parse_search_results_skips_empty_metadata_lists(self, mock_auth):
        """Test parse_search_results ignores fields present with an empty value list"""
        manager = DiscoveryManager(mock_auth)
        results = {"_embedded": {"searchResult": {"_embedded": {"objects": [
            {"_embedded": {"indexableObject": {"uuid": "u1", "metadata": {"dc.title": [], "dc.subject": [{"value": "S"}]}}}}
        ]}}}}
        parsed = manager.parse_search_results(results)[0]
        assert "dc.title" not in parsed["metadata"] and "title" not in parsed
        assert parsed["metadata"]["dc.subject"] == ["S"]

    def test_parse_search_results_person_without_names(self, mock_auth):
        """Test Person entities without family/given name get no person_name"""
        manager = DiscoveryManager(mock_auth)
        results = {"_embedded": {"searchResult": {"_embedded": {"objects": [
            {"_embedded": {"indexableObject": {"uuid": "p1", "entityType": "Person", "metadata": {}}}}
        ]}}}}
        parsed = manager.parse_search_results(results)[0]
        assert parsed["entityType"] == "Person" and "person_name" not in parsed

    def test_get_all_results_skips_objects_without_indexable_object(self, mock_auth):
        """Test get_all_results ignores search hits that carry no indexableObject"""
        manager = DiscoveryManager(mock_auth)
        manager.search_objects = Mock(return_value={"_embedded": {"searchResult": {
            "_embedded": {"objects": [{"_embedded": {}}, {"_embedded": {"indexableObject": {"uuid": "a"}}}]},
            "page": {"number": 0, "totalPages": 1},
        }}})
        assert manager.get_all_results("*:*") == [{"uuid": "a"}]
