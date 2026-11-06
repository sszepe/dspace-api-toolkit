"""
Tests for metadata.py: MetadataPatch builder, generic metadata PATCH and the metadata registry
"""

import json

import pytest
import responses

from dspace_api.exceptions import DSpaceError
from dspace_api.metadata import (DSO_PATHS, MetadataManager, MetadataPatch, as_operations, field_name,
                                 metadata_value, parse_field)

ITEM = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def mgr(http_auth):
    return MetadataManager(http_auth)


def last(http):
    return http.calls[-1].request


def body(http):
    return json.loads(last(http).body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_metadata_value(self):
        assert metadata_value("x") == {"value": "x"}
        assert metadata_value(None) == {"value": ""}
        assert metadata_value(5, language="en") == {"value": "5", "language": "en"}
        assert metadata_value({"value": "a", "language": "de"}, language="en", authority="A", confidence=600) == {
            "value": "a", "language": "en", "authority": "A", "confidence": 600}
        with pytest.raises(ValueError):
            metadata_value({"language": "en"})

    def test_parse_field(self):
        assert parse_field("dc.title") == ("dc", "title", None)
        assert parse_field(" dc.contributor.author ") == ("dc", "contributor", "author")
        for bad in ("dc", "dc..x", "a.b.c.d", "", None):
            with pytest.raises(ValueError):
                parse_field(bad)

    def test_field_name(self):
        assert field_name({"schema": {"prefix": "dc"}, "element": "title", "qualifier": None}) == "dc.title"
        assert field_name({"schema": "dc", "element": "date", "qualifier": "issued"}) == "dc.date.issued"
        assert field_name({"_embedded": {"schema": {"prefix": "x"}}, "element": "e"}) == "x.e"
        assert field_name({"element": "e", "qualifier": "q"}) == "e.q"

    def test_as_operations(self):
        assert as_operations(MetadataPatch().remove("dc.title")) == [{"op": "remove", "path": "/metadata/dc.title"}]
        assert as_operations(iter([{"op": "x"}])) == [{"op": "x"}]


class TestMetadataPatch:
    def test_all_operations(self):
        patch = (MetadataPatch()
                 .set("dc.subject", ["a", {"value": "b"}], language="en")
                 .set("dc.type", "Text")
                 .replace("dc.title", "T")
                 .replace("dc.rights", ["r1", "r2"])
                 .remove("dc.description")
                 .remove("dc.subject", 1)
                 .append("dc.contributor.author", "Doe")
                 .insert("dc.title", 0, "Main", language="de")
                 .replace_value("dc.title", "1", "Sub")
                 .set_property("dc.title", 0, "language", "fr")
                 .move("dc.contributor.author", 2, 0))
        assert patch.operations == [
            # DSpace keeps only the first value of an array -> remove + one append per value
            {"op": "remove", "path": "/metadata/dc.subject"},
            {"op": "add", "path": "/metadata/dc.subject/-", "value": {"value": "a", "language": "en"}},
            {"op": "add", "path": "/metadata/dc.subject/-", "value": {"value": "b", "language": "en"}},
            {"op": "remove", "path": "/metadata/dc.type"},
            {"op": "add", "path": "/metadata/dc.type/-", "value": {"value": "Text"}},
            {"op": "replace", "path": "/metadata/dc.title", "value": [{"value": "T"}]},
            {"op": "remove", "path": "/metadata/dc.rights"},
            {"op": "add", "path": "/metadata/dc.rights/-", "value": {"value": "r1"}},
            {"op": "add", "path": "/metadata/dc.rights/-", "value": {"value": "r2"}},
            {"op": "remove", "path": "/metadata/dc.description"},
            {"op": "remove", "path": "/metadata/dc.subject/1"},
            {"op": "add", "path": "/metadata/dc.contributor.author/-", "value": {"value": "Doe"}},
            {"op": "add", "path": "/metadata/dc.title/0", "value": {"value": "Main", "language": "de"}},
            {"op": "replace", "path": "/metadata/dc.title/1", "value": {"value": "Sub"}},
            {"op": "replace", "path": "/metadata/dc.title/0/language", "value": "fr"},
            {"op": "move", "path": "/metadata/dc.contributor.author/0",
             "from": "/metadata/dc.contributor.author/2"},
        ]
        assert patch.fields() == ["dc.subject", "dc.type", "dc.title", "dc.rights", "dc.description",
                                  "dc.contributor.author"]
        assert len(patch) == 16 and list(patch)[0]["op"] == "remove"
        assert repr(patch) == "MetadataPatch('/metadata', 16 op(s))"

    def test_invalid(self):
        with pytest.raises(ValueError):
            MetadataPatch().set("title", "x")
        with pytest.raises(ValueError):
            MetadataPatch().set_property("dc.title", 0, "place", 1)

    def test_section_and_file_paths_and_extend(self):
        section = MetadataPatch.for_section("traditionalpageone").set("dc.title", "T")
        upload = MetadataPatch.for_file("upload", "2").remove("dc.description")
        assert section.operations == [{"op": "add", "path": "/sections/traditionalpageone/dc.title",
                                       "value": [{"value": "T"}]}]      # sections keep add-with-array
        assert MetadataPatch.for_section("p").replace("dc.subject", ["a", "b"]).operations[0]["op"] == "replace"
        assert section.archived is False and MetadataPatch().archived is True
        assert upload.operations[0]["path"] == "/sections/upload/files/2/metadata/dc.description"
        assert section.fields() == ["dc.title"] and upload.fields() == ["dc.description"]
        combined = MetadataPatch("/x/").extend(section)
        assert combined.base_path == "/x" and len(combined) == 1


# ---------------------------------------------------------------------------
# Generic object metadata
# ---------------------------------------------------------------------------

class TestObjectMetadata:
    def test_get_metadata_uses_all_languages(self, mgr, http, api_url):
        http.add(responses.GET, api_url(f"/core/items/{ITEM}"), json={"metadata": {"dc.title": [{"value": "T"}]}})
        assert mgr.get_metadata("item", ITEM) == {"dc.title": [{"value": "T"}]}
        assert last(http).params == {"projection": "allLanguages"}
        http.add(responses.GET, api_url(f"/core/sites/{ITEM}"), json={})
        assert mgr.get_metadata("site", ITEM, all_languages=False) == {}

    def test_unknown_dso_type(self, mgr):
        with pytest.raises(ValueError):
            mgr.get_metadata("thing", ITEM)

    def test_patch_metadata(self, mgr, http, api_url):
        http.add(responses.PATCH, api_url(f"/eperson/groups/{ITEM}"), json={"uuid": ITEM})
        assert mgr.patch_metadata("group", ITEM, MetadataPatch().set("dc.description", "d"))["uuid"] == ITEM
        assert body(http) == [{"op": "remove", "path": "/metadata/dc.description"},
                              {"op": "add", "path": "/metadata/dc.description/-", "value": {"value": "d"}}]
        mgr.patch_metadata("group", ITEM, [{"op": "remove", "path": "/metadata/dc.title"}], check_fields=True)
        assert body(http)[0]["op"] == "remove"

    def test_patch_metadata_empty(self, mgr):
        with pytest.raises(ValueError):
            mgr.patch_metadata("item", ITEM, MetadataPatch())

    def test_patch_metadata_check_fields(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/metadatafields/search/byFieldName"),
                 json=hal_page("metadatafields", []))
        with pytest.raises(ValueError, match="local.unknown"):
            mgr.patch_metadata("item", ITEM, MetadataPatch().set("local.unknown", "x"), check_fields=True)
        assert all(c.request.method == "GET" for c in http.calls)

    def test_patch_metadata_check_fields_ok(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/metadatafields/search/byFieldName"),
                 json=hal_page("metadatafields", [{"id": 1, "element": "title", "qualifier": None,
                                                   "_embedded": {"schema": {"prefix": "dc"}}}]))
        http.add(responses.PATCH, api_url(f"/core/items/{ITEM}"), json={})
        mgr.patch_metadata("item", ITEM, MetadataPatch().set("dc.title", "x"), check_fields=True)
        assert last(http).method == "PATCH"

    def test_dso_paths(self):
        assert DSO_PATHS["eperson"] == "/eperson/epersons"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

DC = {"id": 1, "prefix": "dc", "namespace": "http://purl.org/dc/terms/"}
LOCAL = {"id": 3, "prefix": "local", "namespace": "http://example.org/local"}


class TestSchemas:
    def test_list_get_find(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/metadataschemas"), json=hal_page("metadataschemas", [DC, LOCAL]))
        http.add(responses.GET, api_url("/core/metadataschemas/1"), json=DC)
        assert mgr.get_schemas() == [DC, LOCAL]
        assert mgr.get_schema(1) == DC
        assert mgr.find_schema("local") == LOCAL
        calls = len(http.calls)
        assert mgr.find_schema("dc") == DC and len(http.calls) == calls  # cached
        assert mgr.find_schema("none") is None
        mgr.find_schema("dc", refresh=True)
        assert len(http.calls) == calls + 2

    def test_create_update_delete(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/core/metadataschemas"), json=LOCAL, status=201)
        assert mgr.create_schema("local", LOCAL["namespace"]) == LOCAL
        assert body(http) == {"prefix": "local", "namespace": LOCAL["namespace"]}
        assert mgr.find_schema("local") == LOCAL  # cached by create

        http.add(responses.GET, api_url("/core/metadataschemas/3"), json=LOCAL)
        http.add(responses.PUT, api_url("/core/metadataschemas/3"), json=dict(LOCAL, namespace="n"))
        assert mgr.update_schema(3, "n")["namespace"] == "n"
        assert body(http) == {"id": 3, "prefix": "local", "namespace": "n"}
        assert "local" not in mgr._schema_cache

        http.add(responses.DELETE, api_url("/core/metadataschemas/3"), status=204)
        mgr._field_cache["x.y"] = None
        assert mgr.delete_schema(3) is True
        assert mgr._field_cache == {} and mgr._schema_cache == {}

    def test_create_schema_invalid(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/core/metadataschemas"), status=422, body="invalid")
        with pytest.raises(DSpaceError) as info:
            mgr.create_schema("a.b", "n")
        assert info.value.status_code == 422


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------

def registry_field(fid, prefix, element, qualifier=None):
    return {"id": fid, "element": element, "qualifier": qualifier, "scopeNote": None,
            "_embedded": {"schema": {"prefix": prefix}}}


TITLE = registry_field(64, "dc", "title")
ALT = registry_field(65, "dc", "title", "alternative")


class TestFields:
    def test_get_field(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/metadatafields/64"), json=TITLE)
        assert mgr.get_field(64) == TITLE
        assert last(http).params == {"embed": "schema"}
        mgr.get_field(64, embed=None)
        assert last(http).params == {}

    def test_by_schema(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/metadatafields/search/bySchema"),
                 json=hal_page("metadatafields", [TITLE, ALT]))
        assert mgr.get_fields_by_schema("dc") == [TITLE, ALT]
        assert last(http).params["schema"] == "dc"

    def test_search_fields(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/metadatafields/search/byFieldName"),
                 json=hal_page("metadatafields", [TITLE, ALT]))
        assert mgr.search_fields(query="tit", schema="dc", element="title", qualifier="alternative",
                                 max_results=1) == [TITLE]
        params = last(http).params
        assert params["query"] == "tit" and params["schema"] == "dc" and params["element"] == "title"
        assert params["qualifier"] == "alternative" and params["embed"] == "schema"
        assert "exactName" not in params

    def test_find_field_verifies_match_and_caches(self, mgr, http, api_url, hal_page):
        # A server that ignores exactName returns more than the match
        http.add(responses.GET, api_url("/core/metadatafields/search/byFieldName"),
                 json=hal_page("metadatafields", [ALT, TITLE]))
        assert mgr.find_field("dc.title") == TITLE
        assert last(http).params["exactName"] == "dc.title"
        calls = len(http.calls)
        assert mgr.field_exists("dc.title") and len(http.calls) == calls
        mgr.find_field("dc.title", refresh=True)
        assert len(http.calls) == calls + 1

    def test_find_field_without_schema_prefix(self, mgr, http, api_url, hal_page):
        bare = {"id": 9, "element": "date", "qualifier": "issued"}
        other_schema = registry_field(10, "dcterms", "date", "issued")
        http.add(responses.GET, api_url("/core/metadatafields/search/byFieldName"),
                 json=hal_page("metadatafields", [other_schema, bare]))
        assert mgr.find_field("dc.date.issued") == bare

    def test_unknown_fields(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/metadatafields/search/byFieldName"),
                 json=hal_page("metadatafields", [TITLE]))
        assert mgr.unknown_fields(["dc.title", "local.x", "local.x"]) == ["local.x"]

    def test_create_field(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/metadataschemas"), json=hal_page("metadataschemas", [DC, LOCAL]))
        created = registry_field(99, "local", "project", "id")
        http.add(responses.POST, api_url("/core/metadatafields"), json=created, status=201)
        assert mgr.create_field("local.project.id", "Project id") == created
        assert last(http).params == {"schemaId": "3"}
        assert body(http) == {"element": "project", "qualifier": "id", "scopeNote": "Project id"}
        assert mgr.find_field("local.project.id") == created

    def test_create_field_unknown_schema(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/metadataschemas"), json=hal_page("metadataschemas", [DC]))
        with pytest.raises(ValueError, match="create_schema"):
            mgr.create_field("nope.x")

    def test_ensure_fields(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/core/metadatafields/search/byFieldName"),
                 json=hal_page("metadatafields", []))
        http.add(responses.GET, api_url("/core/metadataschemas"), json=hal_page("metadataschemas", [LOCAL]))
        http.add(responses.POST, api_url("/core/metadatafields"), json={"id": 1}, status=201)
        assert mgr.ensure_fields(["local.a", "local.b"], dry_run=True) == ["local.a", "local.b"]
        assert not [c for c in http.calls if c.request.method == "POST"]
        assert mgr.ensure_fields(["local.a", "local.b"]) == ["local.a", "local.b"]
        assert len([c for c in http.calls if c.request.method == "POST"]) == 2

    def test_update_and_delete_field(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/core/metadatafields/65"), json=ALT)
        http.add(responses.PUT, api_url("/core/metadatafields/65"), json=dict(ALT, scopeNote="n"))
        mgr._field_cache["dc.title.alternative"] = ALT
        assert mgr.update_field(65, "n")["scopeNote"] == "n"
        assert body(http) == {"id": 65, "element": "title", "qualifier": "alternative", "scopeNote": "n"}
        assert mgr._field_cache == {}
        http.add(responses.DELETE, api_url("/core/metadatafields/65"), status=204)
        mgr._field_cache["x.y"] = None
        assert mgr.delete_field(65) is True and mgr._field_cache == {}
