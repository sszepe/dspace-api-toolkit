"""
Mock API response data for DSpace API tests
"""

class MockAPIResponses:
    """
    Container for mock API response data
    """

    @property
    def auth_status_authenticated(self):
        return {
            "authenticated": True,
            "email": "admin@dspace.org",
            "fullname": "DSpace Administrator",
            "uuid": "admin-uuid-123"
        }

    @property
    def auth_status_unauthenticated(self):
        return {
            "authenticated": False
        }

    @property
    def items_list(self):
        return {
            "_embedded": {
                "items": [
                    {
                        "uuid": "item-1-uuid",
                        "name": "Test Item 1",
                        "handle": "123456789/1",
                        "type": "item",
                        "entityType": "Publication",
                        "inArchive": True,
                        "discoverable": True,
                        "withdrawn": False,
                        "metadata": {
                            "dc.title": [{"value": "Test Publication 1"}],
                            "dc.contributor.author": [{"value": "Author One"}]
                        }
                    },
                    {
                        "uuid": "item-2-uuid",
                        "name": "Test Item 2",
                        "handle": "123456789/2",
                        "type": "item",
                        "entityType": "Publication",
                        "inArchive": True,
                        "discoverable": True,
                        "withdrawn": False,
                        "metadata": {
                            "dc.title": [{"value": "Test Publication 2"}],
                            "dc.contributor.author": [{"value": "Author Two"}]
                        }
                    }
                ]
            },
            "page": {
                "size": 20,
                "totalElements": 2,
                "totalPages": 1,
                "number": 0
            }
        }

    @property
    def single_item(self):
        return {
            "uuid": "single-item-uuid",
            "name": "Single Test Item",
            "handle": "123456789/single",
            "type": "item",
            "entityType": "Publication",
            "inArchive": True,
            "discoverable": True,
            "withdrawn": False,
            "lastModified": "2025-01-01T12:00:00Z",
            "metadata": {
                "dc.title": [{"value": "Single Test Publication", "language": "en"}],
                "dc.contributor.author": [
                    {"value": "Primary Author", "language": None, "authority": None, "confidence": -1},
                    {"value": "Secondary Author", "language": None, "authority": None, "confidence": -1}
                ],
                "dc.date.issued": [{"value": "2025"}],
                "dc.description": [{"value": "A test publication for unit testing"}]
            },
            "_links": {
                "self": {"href": "http://localhost:8080/server/api/core/items/single-item-uuid"}
            }
        }

    @property
    def workspace_item_created(self):
        return {
            "id": 123,
            "uuid": "workspace-item-uuid",
            "lastModified": "2025-01-01T12:00:00Z",
            "sections": {
                "publication": {},
                "publication_type": {},
                "publication_indexing": {}
            },
            "item": {
                "uuid": "workspace-item-item-uuid",
                "name": "Workspace Item",
                "type": "item",
                "entityType": "Publication",
                "inArchive": False,
                "discoverable": False,
                "withdrawn": False,
                "metadata": {
                    "dspace.entity.type": [{"value": "Publication"}]
                }
            },
            "_links": {
                "self": {"href": "http://localhost:8080/server/api/submission/workspaceitems/123"}
            }
        }

    @property
    def workspace_item_with_metadata(self):
        return {
            "id": 123,
            "uuid": "workspace-item-uuid",
            "lastModified": "2025-01-01T12:00:00Z",
            "sections": {
                "publication": {
                    "dc.title": [{"value": "Test Workspace Publication", "language": "en"}],
                    "dc.contributor.author": [{"value": "Test Author"}],
                    "dc.date.issued": [{"value": "2025"}]
                },
                "publication_type": {
                    "dc.type": [{
                        "value": "still image",
                        "authority": "publication-coar-types:c_ecc8",
                        "confidence": 600
                    }]
                },
                "publication_indexing": {
                    "dc.description": [{"value": "Test description"}]
                }
            },
            "item": {
                "uuid": "workspace-item-item-uuid",
                "name": "Test Workspace Publication",
                "type": "item",
                "entityType": "Publication",
                "inArchive": False,
                "discoverable": False,
                "withdrawn": False
            }
        }

    @property
    def collections_list(self):
        return {
            "_embedded": {
                "collections": [
                    {
                        "uuid": "collection-1-uuid",
                        "name": "Test Collection 1",
                        "handle": "123456789/col1",
                        "type": "collection",
                        "metadata": {
                            "dc.title": [{"value": "Test Collection 1"}],
                            "dc.description": [{"value": "First test collection"}]
                        }
                    },
                    {
                        "uuid": "collection-2-uuid",
                        "name": "Test Collection 2",
                        "handle": "123456789/col2",
                        "type": "collection",
                        "metadata": {
                            "dc.title": [{"value": "Test Collection 2"}],
                            "dc.description": [{"value": "Second test collection"}]
                        }
                    }
                ]
            },
            "page": {
                "size": 20,
                "totalElements": 2,
                "totalPages": 1,
                "number": 0
            }
        }

    @property
    def single_collection(self):
        return {
            "uuid": "single-collection-uuid",
            "name": "Single Test Collection",
            "handle": "123456789/singlecol",
            "type": "collection",
            "metadata": {
                "dc.title": [{"value": "Single Test Collection"}],
                "dc.description": [{"value": "A collection for testing"}]
            },
            "_links": {
                "self": {"href": "http://localhost:8080/server/api/core/collections/single-collection-uuid"},
                "parentCommunity": {"href": "http://localhost:8080/server/api/core/communities/parent-community-uuid"}
            }
        }

    @property
    def collection_created(self):
        return {
            "uuid": "new-collection-uuid",
            "name": "Test Collection",
            "handle": "123456789/newcol",
            "type": "collection",
            "metadata": {
                "dc.title": [{"value": "Test Collection"}],
                "dc.description": [{"value": "A test collection created via API"}]
            },
            "_links": {
                "self": {"href": "http://localhost:8080/server/api/core/collections/new-collection-uuid"},
                "parentCommunity": {"href": "http://localhost:8080/server/api/core/communities/parent-community-uuid"}
            }
        }

    @property
    def collection_updated(self):
        return {
            "uuid": "new-collection-uuid",
            "name": "Test Collection New",
            "handle": "123456789/23524",
            "type": "collection",
            "metadata": {
                "dc.title": [{"value": "Test Collection New"}],
                "dspace.entity.type": [{"value": "Publication"}],
                "cris.workspace.shared": [{"value": "true"}],
            },
        }

    @property
    def bundles_list(self):
        return {
            "_embedded": {
                "bundles": [
                    {
                        "uuid": "bundle-1-uuid",
                        "name": "ORIGINAL",
                        "type": "bundle",
                        "metadata": {}
                    },
                    {
                        "uuid": "bundle-2-uuid",
                        "name": "THUMBNAIL",
                        "type": "bundle",
                        "metadata": {}
                    }
                ]
            }
        }

    @property
    def single_bundle(self):
        return {
            "uuid": "single-bundle-uuid",
            "name": "ORIGINAL",
            "type": "bundle",
            "metadata": {},
            "_links": {
                "self": {"href": "http://localhost:8080/server/api/core/bundles/single-bundle-uuid"},
                "bitstreams": {"href": "http://localhost:8080/server/api/core/bundles/single-bundle-uuid/bitstreams"}
            }
        }

    @property
    def bitstreams_list(self):
        return {
            "_embedded": {
                "bitstreams": [
                    {
                        "uuid": "bitstream-1-uuid",
                        "name": "document.pdf",
                        "sizeBytes": 2048,
                        "checkSum": {
                            "checkSumAlgorithm": "MD5",
                            "value": "abc123def456"
                        },
                        "sequenceId": 1,
                        "type": "bitstream"
                    }
                ]
            }
        }

    @property
    def single_bitstream(self):
        return {
            "uuid": "single-bitstream-uuid",
            "name": "test-document.pdf",
            "sizeBytes": 1024,
            "checkSum": {
                "checkSumAlgorithm": "MD5",
                "value": "test123hash456"
            },
            "sequenceId": 1,
            "type": "bitstream",
            "metadata": {
                "dc.title": [{"value": "Test Document"}]
            },
            "_links": {
                "self": {"href": "http://localhost:8080/server/api/core/bitstreams/single-bitstream-uuid"},
                "content": {"href": "http://localhost:8080/server/api/core/bitstreams/single-bitstream-uuid/content"}
            }
        }

    @property
    def discovery_search_results(self):
        return {
            "_embedded": {
                "searchResult": {
                    "_embedded": {
                        "objects": [
                            {
                                "_embedded": {
                                    "indexableObject": {
                                        "uuid": "discovered-item-1",
                                        "name": "Discovered Item 1",
                                        "handle": "123456789/disc1",
                                        "type": "item",
                                        "entityType": "Publication",
                                        "lastModified": "2025-01-01T10:00:00Z",
                                        "metadata": {
                                            "dc.title": [{"value": "Discovered Publication 1"}],
                                            "dc.contributor.author": [{"value": "Discovery Author"}],
                                            "dc.date.issued": [{"value": "2024"}],
                                            "dc.subject": [{"value": "testing"}, {"value": "discovery"}]
                                        }
                                    }
                                }
                            },
                            {
                                "_embedded": {
                                    "indexableObject": {
                                        "uuid": "discovered-person-1",
                                        "name": "Beethoven, Ludwig van",
                                        "handle": "123456789/person1",
                                        "type": "item",
                                        "entityType": "Person",
                                        "metadata": {
                                            "dc.title": [{"value": "Beethoven, Ludwig van"}],
                                            "person.familyName": [{"value": "Beethoven"}],
                                            "person.givenName": [{"value": "Ludwig van"}],
                                            "person.identifier.gnd": [{"value": "118508288"}]
                                        }
                                    }
                                }
                            }
                        ]
                    }
                }
            },
            "page": {
                "size": 10,
                "totalElements": 2,
                "totalPages": 1,
                "number": 0
            }
        }

    @property
    def vocabulary_entries(self):
        return {
            "_embedded": {
                "vocabularyEntryDetails": [
                    {
                        "id": "coar-c_ecc8",
                        "display": "still image",
                        "value": "still image",
                        "authority": "publication-coar-types:c_ecc8",
                        "selectable": True,
                        "otherInformation": {
                            "note": "A static visual representation.",
                            "hasChildren": "false",
                            "id": "c_ecc8"
                        },
                        "_links": {
                            "self": {"href": "http://localhost:8080/server/api/submission/vocabularyEntryDetails/coar-c_ecc8"}
                        }
                    },
                    {
                        "id": "coar-c_6501",
                        "display": "journal article",
                        "value": "journal article",
                        "authority": "publication-coar-types:c_6501",
                        "selectable": True,
                        "otherInformation": {
                            "note": "A article published in a journal.",
                            "hasChildren": "false",
                            "id": "c_6501"
                        },
                        "_links": {
                            "self": {"href": "http://localhost:8080/server/api/submission/vocabularyEntryDetails/coar-c_6501"}
                        }
                    }
                ]
            },
            "page": {
                "size": 100,
                "totalElements": 2,
                "totalPages": 1,
                "number": 0
            }
        }

    @property
    def vocabularies_list(self):
        return {
            "_embedded": {
                "vocabularies": [
                    {
                        "id": "publication-coar-types",
                        "name": "publication-coar-types",
                        "scrollable": True,
                        "hierarchical": True,
                        "_links": {
                            "entries": {"href": "http://localhost:8080/server/api/submission/vocabularies/publication-coar-types/entries"}
                        }
                    },
                    {
                        "id": "common_iso_languages",
                        "name": "common_iso_languages",
                        "scrollable": True,
                        "hierarchical": False,
                        "_links": {
                            "entries": {"href": "http://localhost:8080/server/api/submission/vocabularies/common_iso_languages/entries"}
                        }
                    }
                ]
            }
        }

    @property
    def workflow_item_response(self):
        return "http://localhost:8080/server/api/workflow/workflowitems/456"

    def get_error_response(self, status_code, message=None):
        """Generate error response for given status code"""
        error_messages = {
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            500: "Internal Server Error"
        }

        return {
            "error": error_messages.get(status_code, "Unknown Error"),
            "message": message or f"HTTP {status_code} error occurred",
            "status": status_code
        }