"""
Tests for the submission features of workspaces.py (BaseManager-based API):
lookups, creation variants, section PATCH helpers, submission config, deposit, submit()
"""

import io
import json

import pytest
import responses

from dspace_api.exceptions import AuthorizationError, DSpaceError, NotFoundError
from dspace_api.metadata import MetadataPatch
from dspace_api.workspaces import SubmissionError, WorkspaceManager

WS = 42
COLL = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ITEM = "11111111-1111-1111-1111-111111111111"
EP = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
BS = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
API = "http://localhost:8080/server/api"
WS_URL = f"{API}/submission/workspaceitems/{WS}"

SECTIONS = [
    {"id": "collection", "sectionType": "collection"},
    {"id": "traditionalpageone", "sectionType": "submission-form"},
    {"id": "traditionalpagetwo", "sectionType": "submission-form"},
    {"id": "upload", "sectionType": "upload"},
    {"id": "license", "sectionType": "license"},
    {"id": "cclicense", "sectionType": "cclicense"},
    {"id": "itemAccessConditions", "sectionType": "access"},
    {"id": "sherpaPolicies", "sectionType": "sherpaPolicies"},
]


def form(*fields):
    return {"rows": [{"fields": [{"selectableMetadata": [{"metadata": f} for f in group]}]}
                     for group in fields] + [{"fields": [{"selectableMetadata": [{}]}, {}]}, {}]}


@pytest.fixture
def mgr(http_auth):
    return WorkspaceManager(http_auth)


@pytest.fixture
def config(http, api_url, hal_page):
    """Submission definition 'traditional' with two forms and the usual sections."""
    http.add(responses.GET, f"{WS_URL}/submissionDefinition", json={"name": "traditional"})
    # DSpace embeds the sections in the definition as a page
    http.add(responses.GET, api_url("/config/submissiondefinitions/traditional"),
             json={"name": "traditional", "_embedded": {"sections": hal_page("sections", SECTIONS)}})
    http.add(responses.GET, api_url("/config/submissionsections/traditionalpageone"),
             json={"id": "traditionalpageone",
                   "_links": {"config": {"href": f"{API}/config/submissionforms/traditionalpageone"}}})
    http.add(responses.GET, api_url("/config/submissionforms/traditionalpageone"),
             json=form(["dc.title"], ["dc.contributor.author", "dc.contributor.editor"], ["dc.title"]))
    # no config link -> falls back to the form of the same name
    http.add(responses.GET, api_url("/config/submissionsections/traditionalpagetwo"), json={"id": "x"})
    http.add(responses.GET, api_url("/config/submissionforms/traditionalpagetwo"),
             json=form(["dc.subject"], ["dc.description.abstract"]))
    return http


def ws_json(sections=None, errors=None):
    return {"id": WS, "type": "workspaceitem", "errors": errors or [],
            "sections": sections if sections is not None else {"traditionalpageone": {}, "traditionalpagetwo": {}}}


def last(http, method=None):
    calls = [c for c in http.calls if method is None or c.request.method == method]
    return calls[-1].request


def patch_body(http):
    return json.loads(last(http, "PATCH").body)


def add_patch(http, response=None, status=200):
    http.add(responses.PATCH, WS_URL, json=response if response is not None else ws_json(), status=status)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

class TestLookups:
    def test_get_and_links(self, mgr, http, api_url):
        http.add(responses.GET, WS_URL, json=ws_json())
        http.add(responses.GET, f"{WS_URL}/item", json={"uuid": ITEM})
        http.add(responses.GET, f"{WS_URL}/collection", json={"uuid": COLL})
        assert mgr.get(WS, embed="item")["id"] == WS
        assert last(http).params == {"embed": "item"}
        assert mgr.get_item_of(WS, all_languages=True)["uuid"] == ITEM
        assert last(http).params == {"projection": "allLanguages"}
        assert mgr.get_collection_of(WS)["uuid"] == COLL

    def test_get_raises(self, mgr, http):
        http.add(responses.GET, WS_URL, status=404)
        with pytest.raises(NotFoundError):
            mgr.get(WS)

    def test_iter_and_find(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/submission/workspaceitems"), json=hal_page("workspaceitems", [ws_json()]))
        http.add(responses.GET, api_url("/submission/workspaceitems/search/findBySubmitter"),
                 json=hal_page("workspaceitems", [ws_json()]))
        assert len(list(mgr.iter_workspace_items(page_size=5))) == 1
        assert last(http).params["size"] == "5"
        assert len(mgr.find_by_submitter(EP)) == 1
        assert last(http).params["uuid"] == EP

    def test_find_by_item(self, mgr, http, api_url):
        url = api_url("/submission/workspaceitems/search/item")
        http.add(responses.GET, url, json=ws_json())
        assert mgr.find_by_item(ITEM)["id"] == WS
        assert last(http).params == {"uuid": ITEM}
        http.replace(responses.GET, url, status=204)
        assert mgr.find_by_item(ITEM) is None

    def test_delete(self, mgr, http):
        http.add(responses.DELETE, WS_URL, status=204)
        assert mgr.delete(WS) is True


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

class TestCreate:
    def test_start_submission(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/submission/workspaceitems"), json=ws_json(), status=201)
        mgr.start_submission(COLL)
        assert last(http).params == {"owningCollection": COLL}
        mgr.start_submission(COLL, "Publication")
        assert last(http).params == {"owningCollection": COLL, "entityType": "Publication"}

    def test_from_external_source(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/submission/workspaceitems"), json=ws_json(), status=201)
        mgr.create_from_external_source(COLL, "/api/integration/externalsources/orcid/entryValues/1")
        request = last(http)
        assert request.headers["Content-Type"] == "text/uri-list"
        assert request.body == f"{API}/integration/externalsources/orcid/entryValues/1"
        with pytest.raises(ValueError):
            mgr.create_from_external_source(COLL, "https://evil.example.org/x")

    def test_from_files(self, mgr, http, api_url, tmp_path):
        path = tmp_path / "refs.bib"
        path.write_bytes(b"@article{x}")
        http.add(responses.POST, api_url("/submission/workspaceitems"),
                 json={"_embedded": {"workspaceitems": [ws_json(), ws_json()]}}, status=201)
        result = mgr.create_from_files([str(path), (b"TY  - JOUR", "a.ris")], collection_uuid=COLL)
        assert len(result) == 2
        request = last(http)
        assert request.params == {"owningCollection": COLL}
        assert b'filename="refs.bib"' in request.body and b'filename="a.ris"' in request.body
        assert request.headers["Content-Type"].startswith("multipart/form-data")

    def test_from_files_single_object_and_no_collection(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/submission/workspaceitems"), json=ws_json(), status=201)
        assert mgr.create_from_files([(b"x", "a.bib")]) == [ws_json()]
        assert last(http).params == {}

    def test_from_files_requires_files(self, mgr):
        with pytest.raises(ValueError):
            mgr.create_from_files([])


# ---------------------------------------------------------------------------
# PATCH plumbing
# ---------------------------------------------------------------------------

class TestApplyPatch:
    def test_patch_and_content_type(self, mgr, http):
        add_patch(http)
        mgr.apply_patch(WS, MetadataPatch.for_section("p").set("dc.title", "T"))
        assert last(http).headers["Content-Type"] == "application/json-patch+json"
        assert patch_body(http) == [{"op": "add", "path": "/sections/p/dc.title", "value": [{"value": "T"}]}]

    def test_empty(self, mgr):
        with pytest.raises(ValueError):
            mgr.apply_patch(WS, [])

    def test_422_becomes_submission_error(self, mgr, http):
        errors = [{"message": "error.validation.required", "paths": ["/sections/p/dc.title"]}]
        http.add(responses.PATCH, WS_URL, status=422, json={"errors": errors})
        with pytest.raises(SubmissionError) as info:
            mgr.apply_patch(WS, [{"op": "remove", "path": "/sections/p/dc.title"}])
        assert info.value.errors == errors and info.value.status_code == 422

    @pytest.mark.parametrize("body", ["not json", "[1]", ""])
    def test_422_with_odd_body(self, mgr, http, body):
        http.add(responses.PATCH, WS_URL, status=422, body=body)
        with pytest.raises(SubmissionError) as info:
            mgr.apply_patch(WS, [{"op": "remove", "path": "/x"}])
        assert info.value.errors == []

    def test_other_errors_pass_through(self, mgr, http):
        http.add(responses.PATCH, WS_URL, status=403)
        with pytest.raises(AuthorizationError):
            mgr.apply_patch(WS, [{"op": "remove", "path": "/x"}])

    def test_validation_errors(self):
        assert WorkspaceManager.validation_errors({"errors": [{"message": "m"}]}) == [{"message": "m"}]
        assert WorkspaceManager.validation_errors({"errors": None}) == []


# ---------------------------------------------------------------------------
# Submission configuration
# ---------------------------------------------------------------------------

class TestConfig:
    def test_definitions(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/config/submissiondefinitions"),
                 json=hal_page("submissiondefinitions", [{"name": "traditional"}]))
        http.add(responses.GET, api_url("/config/submissiondefinitions/traditional"), json={"name": "traditional"})
        http.add(responses.GET, api_url("/config/submissiondefinitions/search/findByCollection"),
                 json={"name": "traditional"})
        assert mgr.get_submission_definitions() == [{"name": "traditional"}]
        assert mgr.get_submission_definition("traditional")["name"] == "traditional"
        assert mgr.get_definition_for_collection(COLL)["name"] == "traditional"
        assert last(http).params == {"uuid": COLL}

    def test_sections_cached(self, mgr, config, api_url):
        assert mgr.get_definition_sections("traditional") == SECTIONS
        assert "embed=sections" in config.calls[-1].request.url
        calls = len(config.calls)
        mgr.get_definition_sections("traditional")
        assert len(config.calls) == calls

    def test_sections_as_plain_list_or_from_subresource(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, api_url("/config/submissiondefinitions/a"),
                 json={"name": "a", "_embedded": {"sections": SECTIONS}})
        assert mgr.get_definition_sections("a") == SECTIONS
        # nothing embedded -> the /sections sub-resource (204 on DSpace 8-10 = no sections)
        http.add(responses.GET, api_url("/config/submissiondefinitions/b"), json={"name": "b"})
        http.add(responses.GET, api_url("/config/submissiondefinitions/b/sections"),
                 json=hal_page("submissionsections", SECTIONS))
        assert mgr.get_definition_sections("b") == SECTIONS
        http.add(responses.GET, api_url("/config/submissiondefinitions/c"), json={"name": "c", "_embedded": {}})
        http.add(responses.GET, api_url("/config/submissiondefinitions/c/sections"), status=204)
        assert mgr.get_definition_sections("c") == []
        http.add(responses.GET, api_url("/config/submissiondefinitions/d"),
                 json={"name": "d", "_embedded": {"sections": {"_embedded": {}}}})
        http.add(responses.GET, api_url("/config/submissiondefinitions/d/sections"), status=204)
        assert mgr.get_definition_sections("d") == []

    def test_single_config_resources(self, mgr, http, api_url, hal_page):
        for path in ("/config/submissionsections/upload", "/config/submissionforms/f",
                     "/config/submissionuploads/upload", "/config/submissionaccessoptions/default",
                     "/config/submissioncclicenses/standard"):
            http.add(responses.GET, api_url(path), json={"id": path})
        http.add(responses.GET, api_url("/config/submissioncclicenses"),
                 json=hal_page("submissioncclicenses", [{"id": "standard"}]))
        assert mgr.get_submission_section("upload")["id"].endswith("upload")
        assert mgr.get_submission_form("f")["id"].endswith("/f")
        assert mgr.get_submission_upload("upload")["id"].startswith("/config/submissionuploads")
        assert mgr.get_access_options()["id"].endswith("default")
        assert mgr.get_cc_license("standard")["id"].endswith("standard")
        assert mgr.get_cc_licenses() == [{"id": "standard"}]

    def test_cc_license_url(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/config/submissioncclicenseUrls/search/rightsByQuestions"),
                 json={"url": "http://creativecommons.org/licenses/by-sa/4.0/"})
        url = mgr.get_cc_license_url("standard", {"commercial": "y", "derivatives": "sa"})
        assert url.endswith("by-sa/4.0/")
        assert last(http).params == {"license": "standard", "answer_commercial": "y", "answer_derivatives": "sa"}

    def test_cc_license_url_lowercase_fallback(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/config/submissioncclicenseUrls/search/rightsByQuestions"), status=404)
        http.add(responses.GET, api_url("/config/submissioncclicenseurls/search/rightsByQuestions"),
                 json={"url": "u"})
        assert mgr.get_cc_license_url("standard") == "u"

    def test_cc_license_url_not_found(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/config/submissioncclicenseUrls/search/rightsByQuestions"), status=404)
        http.add(responses.GET, api_url("/config/submissioncclicenseurls/search/rightsByQuestions"), status=404)
        with pytest.raises(NotFoundError):
            mgr.get_cc_license_url("standard")

    def test_cc_license_url_other_error(self, mgr, http, api_url):
        http.add(responses.GET, api_url("/config/submissioncclicenseUrls/search/rightsByQuestions"), status=400)
        with pytest.raises(DSpaceError):
            mgr.get_cc_license_url("standard")


# ---------------------------------------------------------------------------
# Sections and metadata routing
# ---------------------------------------------------------------------------

class TestSections:
    def test_section_types_and_names(self, mgr, config):
        assert list(mgr.section_types(WS))[:3] == ["collection", "traditionalpageone", "traditionalpagetwo"]
        assert mgr.section_name(WS, "access") == "itemAccessConditions"
        with pytest.raises(ValueError, match="duplicates"):
            mgr.section_name(WS, "duplicates")

    def test_form_fields(self, mgr, config):
        assert mgr.form_fields("traditionalpageone") == ["dc.title", "dc.contributor.author", "dc.contributor.editor"]
        assert mgr.form_fields("traditionalpagetwo") == ["dc.subject", "dc.description.abstract"]
        calls = len(config.calls)
        mgr.form_fields("traditionalpageone")
        assert len(config.calls) == calls

    def test_route_metadata(self, mgr, config):
        config.add(responses.GET, WS_URL, json=ws_json())
        routed, unroutable = mgr.route_metadata(WS, {"dc.title": "T", "dc.subject": ["a"], "local.x": "y"})
        assert routed == {"traditionalpageone": {"dc.title": "T"}, "traditionalpagetwo": {"dc.subject": ["a"]}}
        assert unroutable == ["local.x"]

    def test_route_metadata_only_active_sections(self, mgr, config):
        ws = ws_json({"traditionalpageone": {}})
        routed, unroutable = mgr.route_metadata(WS, {"dc.subject": "a"}, ws)
        assert routed == {} and unroutable == ["dc.subject"]
        # no sections in the JSON at all -> every form counts
        routed, _ = mgr.route_metadata(WS, {"dc.subject": "a"}, ws_json({}))
        assert routed == {"traditionalpagetwo": {"dc.subject": "a"}}

    def test_fill_metadata(self, mgr, config):
        config.add(responses.GET, WS_URL, json=ws_json())
        add_patch(config)
        mgr.fill_metadata(WS, {"dc.title": "T", "dc.subject": ["a", "b"]})
        assert patch_body(config) == [
            {"op": "add", "path": "/sections/traditionalpageone/dc.title", "value": [{"value": "T"}]},
            {"op": "add", "path": "/sections/traditionalpagetwo/dc.subject",
             "value": [{"value": "a"}, {"value": "b"}]},
        ]

    def test_fill_metadata_strict_and_lenient(self, mgr, config):
        config.add(responses.GET, WS_URL, json=ws_json())
        with pytest.raises(ValueError, match="local.x"):
            mgr.fill_metadata(WS, {"local.x": "y"})
        # lenient with nothing routable: no PATCH, returns the item
        assert mgr.fill_metadata(WS, {"local.x": "y"}, strict=False)["id"] == WS
        assert not [c for c in config.calls if c.request.method == "PATCH"]

    def test_patch_section_metadata(self, mgr, http):
        add_patch(http)
        mgr.patch_section_metadata(WS, "p", {"dc.title": "T"})
        assert patch_body(http)[0]["path"] == "/sections/p/dc.title"
        mgr.patch_section_metadata(WS, "p", MetadataPatch.for_section("p").remove("dc.title"))
        assert patch_body(http) == [{"op": "remove", "path": "/sections/p/dc.title"}]


# ---------------------------------------------------------------------------
# Upload section
# ---------------------------------------------------------------------------

UPLOADED = ws_json({
    "traditionalpageone": {},
    "upload": {"primary": None, "files": [
        {"uuid": BS, "metadata": {"dc.title": [{"value": "thesis.pdf"}]}},
        {"uuid": "other", "metadata": {}},
    ]},
    "license": {"granted": False},
})


class TestUpload:
    def test_upload_file(self, mgr, http, tmp_path):
        path = tmp_path / "thesis.pdf"
        path.write_bytes(b"%PDF")
        http.add(responses.POST, WS_URL, json=UPLOADED, status=201)
        mgr.upload_file(WS, str(path))
        request = last(http)
        assert b'filename="thesis.pdf"' in request.body and b"application/pdf" in request.body
        mgr.upload_file(WS, io.BytesIO(b"x"), name="data.zzz-unknown")
        assert b"application/octet-stream" in last(http).body
        mgr.upload_file(WS, b"x", name="a.txt", mime_type="text/x-custom")
        assert b"text/x-custom" in last(http).body

    def test_upload_is_streamed_without_properties_part(self, mgr, http):
        http.add(responses.POST, WS_URL, json=UPLOADED, status=201)
        progress = []
        mgr.upload_file(WS, io.BytesIO(b"x" * 1000), name="big.bin", progress=lambda d, t: progress.append(d),
                        timeout=5)
        request = last(http)
        assert int(request.headers["Content-Length"]) == len(request.body)
        assert b'name="properties"' not in request.body and request.body.count(b"Content-Disposition") == 1
        assert progress == [1000]

    def test_uploaded_and_find_file(self):
        files = WorkspaceManager.uploaded_files(UPLOADED)
        assert [(s, i) for s, i, _ in files] == [("upload", 0), ("upload", 1)]
        assert WorkspaceManager.find_file(UPLOADED, "thesis.pdf")[1] == 0
        assert WorkspaceManager.find_file(UPLOADED, "other")[1] == 1
        with pytest.raises(LookupError):
            WorkspaceManager.find_file(UPLOADED, "missing")
        assert WorkspaceManager.uploaded_files({}) == []

    def test_file_metadata(self, mgr, config):
        add_patch(config)
        mgr.patch_file_metadata(WS, 1, {"dc.description": "d"})
        assert patch_body(config)[0]["path"] == "/sections/upload/files/1/metadata/dc.description"
        mgr.patch_file_metadata(WS, 0, MetadataPatch.for_file("upload2", 0).remove("dc.title"), section="upload2")
        assert patch_body(config) == [{"op": "remove", "path": "/sections/upload2/files/0/metadata/dc.title"}]

    def test_access_condition_builder(self):
        from datetime import date
        assert WorkspaceManager.access_condition("openaccess") == {"name": "openaccess"}
        assert WorkspaceManager.access_condition("lease", "2026-01-01T00:00", date(2027, 1, 1)) == {
            "name": "lease", "startDate": "2026-01-01", "endDate": "2027-01-01"}

    def test_file_access_conditions(self, mgr, config):
        add_patch(config)
        mgr.add_file_access_condition(WS, 0, "embargo", start_date="2027-01-01")
        assert patch_body(config) == [{"op": "add", "path": "/sections/upload/files/0/accessConditions/-",
                                       "value": {"name": "embargo", "startDate": "2027-01-01"}}]
        mgr.set_file_access_conditions(WS, 1, [{"name": "administrator"}, {"name": "lease", "endDate": "2027-01-01"}])
        assert patch_body(config) == [{"op": "add", "path": "/sections/upload/files/1/accessConditions",
                                       "value": [{"name": "administrator"},
                                                 {"name": "lease", "endDate": "2027-01-01"}]}]
        mgr.remove_file_access_condition(WS, 0)
        assert patch_body(config) == [{"op": "remove", "path": "/sections/upload/files/0/accessConditions"}]
        mgr.remove_file_access_condition(WS, 0, 1, section="up")
        assert patch_body(config) == [{"op": "remove", "path": "/sections/up/files/0/accessConditions/1"}]

    def test_file_operations(self, mgr, config):
        add_patch(config)
        mgr.remove_file(WS, 1)
        assert patch_body(config) == [{"op": "remove", "path": "/sections/upload/files/1"}]
        mgr.move_file(WS, 1, 0)
        assert patch_body(config) == [{"op": "move", "from": "/sections/upload/files/1",
                                       "path": "/sections/upload/files/0"}]
        mgr.set_primary_file(WS, BS)
        assert patch_body(config) == [{"op": "add", "path": "/sections/upload/primary", "value": BS}]
        mgr.clear_primary_file(WS)
        assert patch_body(config) == [{"op": "remove", "path": "/sections/upload/primary"}]


# ---------------------------------------------------------------------------
# license / access / cclicense / sherpa
# ---------------------------------------------------------------------------

class TestOtherSections:
    def test_license(self, mgr, config):
        add_patch(config)
        mgr.grant_license(WS)
        assert patch_body(config) == [{"op": "add", "path": "/sections/license/granted", "value": True}]
        mgr.grant_license(WS, granted=False, section="lic")
        assert patch_body(config) == [{"op": "add", "path": "/sections/lic/granted", "value": False}]

    def test_item_access(self, mgr, config):
        add_patch(config)
        mgr.add_access_condition(WS, "embargo", start_date="2027-01-01")
        assert patch_body(config) == [{"op": "add", "path": "/sections/itemAccessConditions/accessConditions/-",
                                       "value": {"name": "embargo", "startDate": "2027-01-01"}}]
        mgr.set_access_conditions(WS, [{"name": "openaccess"}])
        assert patch_body(config)[0]["value"] == [{"name": "openaccess"}]
        mgr.remove_access_condition(WS)
        assert patch_body(config) == [{"op": "remove", "path": "/sections/itemAccessConditions/accessConditions"}]
        mgr.remove_access_condition(WS, 0)
        assert patch_body(config)[0]["path"].endswith("/accessConditions/0")
        mgr.set_discoverable(WS, 0)
        assert patch_body(config) == [{"op": "replace", "path": "/sections/itemAccessConditions/discoverable",
                                       "value": False}]

    def test_cc_and_sherpa(self, mgr, config):
        add_patch(config)
        mgr.set_cc_license(WS, "http://creativecommons.org/licenses/by/4.0/")
        assert patch_body(config) == [{"op": "add", "path": "/sections/cclicense/uri",
                                       "value": "http://creativecommons.org/licenses/by/4.0/"}]
        mgr.remove_cc_license(WS)
        assert patch_body(config) == [{"op": "remove", "path": "/sections/cclicense/uri"}]
        mgr.refresh_sherpa_policies(WS)
        assert patch_body(config) == [{"op": "remove", "path": "/sections/sherpaPolicies/retrievalTime"}]


# ---------------------------------------------------------------------------
# Deposit and submit()
# ---------------------------------------------------------------------------

class TestDeposit:
    def test_deposit(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/workflow/workflowitems"), json={"id": 7}, status=201)
        assert mgr.deposit(WS) == {"id": 7}
        request = last(http)
        assert request.headers["Content-Type"] == "text/uri-list" and request.body == WS_URL

    def test_deposit_without_body(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/workflow/workflowitems"), status=201, body="")
        assert mgr.deposit(WS) == {}

    def test_deposit_incomplete(self, mgr, http, api_url):
        errors = [{"message": "error.validation.license.notgranted", "paths": ["/sections/license"]}]
        http.add(responses.POST, api_url("/workflow/workflowitems"), status=422, body="unprocessable")
        http.add(responses.GET, WS_URL, json=ws_json(errors=errors))
        with pytest.raises(SubmissionError) as info:
            mgr.deposit(WS)
        assert info.value.errors == errors and info.value.workspace_item["id"] == WS

    def test_deposit_other_error(self, mgr, http, api_url):
        http.add(responses.POST, api_url("/workflow/workflowitems"), status=403)
        with pytest.raises(AuthorizationError):
            mgr.deposit(WS)


@pytest.fixture
def submit_server(config, api_url):
    """A server that accepts the whole submit() flow."""
    config.add(responses.POST, api_url("/submission/workspaceitems"), json=ws_json(), status=201)
    config.add(responses.GET, WS_URL, json=ws_json())
    config.add(responses.PATCH, WS_URL, json=UPLOADED)
    config.add(responses.POST, WS_URL, json=UPLOADED, status=201)
    config.add(responses.GET, f"{WS_URL}/item", json={"uuid": ITEM})
    config.add(responses.POST, api_url("/workflow/workflowitems"), json={"id": 7}, status=201)
    config.add(responses.DELETE, WS_URL, status=204)
    return config


def patches(http):
    return [json.loads(c.request.body) for c in http.calls if c.request.method == "PATCH"]


class TestSubmit:
    def test_full_submission(self, mgr, submit_server):
        result = mgr.submit(COLL, {"dc.title": "T"}, files=[(b"%PDF", "thesis.pdf")],
                            file_access_conditions=[{"name": "embargo", "startDate": "2027-01-01"}],
                            access_conditions=[{"name": "openaccess"}], entity_type="Publication")
        assert result["item_uuid"] == ITEM and result["workflow_item"] == {"id": 7} and result["errors"] == []
        ops = [p[0] for p in patches(submit_server)]
        assert ops[0]["path"] == "/sections/traditionalpageone/dc.title"
        assert ops[1]["path"] == "/sections/upload/files/0/accessConditions"
        assert ops[2]["path"] == "/sections/upload/files/1/accessConditions"
        assert ops[3]["path"] == "/sections/itemAccessConditions/accessConditions"
        assert ops[4] == {"op": "add", "path": "/sections/license/granted", "value": True}
        start = next(c.request for c in submit_server.calls if c.request.method == "POST")
        assert start.params == {"owningCollection": COLL, "entityType": "Publication"}

    def test_without_deposit_and_license(self, mgr, submit_server):
        result = mgr.submit(COLL, {"dc.title": "T"}, grant_license=False, deposit=False)
        assert result["workflow_item"] is None
        assert len(patches(submit_server)) == 1
        assert not [c for c in submit_server.calls if c.request.url.endswith("/workflow/workflowitems")]

    def test_plain_file_source(self, mgr, submit_server, tmp_path):
        path = tmp_path / "a.txt"
        path.write_text("x")
        mgr.submit(COLL, {"dc.title": "T"}, files=[str(path)], deposit=False)
        assert any(c.request.method == "POST" and c.request.url == WS_URL for c in submit_server.calls)

    def test_no_license_section(self, mgr, http, api_url, hal_page):
        http.add(responses.GET, f"{WS_URL}/submissionDefinition", json={"name": "simple"})
        http.add(responses.GET, api_url("/config/submissiondefinitions/simple"),
                 json={"name": "simple", "_embedded": {"sections": hal_page(
                     "sections", [{"id": "page", "sectionType": "submission-form"}])}})
        http.add(responses.GET, api_url("/config/submissionsections/page"), json={})
        http.add(responses.GET, api_url("/config/submissionforms/page"), json=form(["dc.title"]))
        http.add(responses.POST, api_url("/submission/workspaceitems"), json=ws_json({}), status=201)
        http.add(responses.GET, WS_URL, json=ws_json({}))
        http.add(responses.PATCH, WS_URL, json=ws_json({}))
        http.add(responses.GET, f"{WS_URL}/item", json={"uuid": ITEM})
        mgr.submit(COLL, {"dc.title": "T"}, deposit=False)
        assert len(patches(http)) == 1

    def test_validation_errors_stop_deposit(self, mgr, submit_server):
        errors = [{"message": "error.validation.required", "paths": ["/sections/traditionalpageone/dc.date.issued"]}]
        submit_server.replace(responses.PATCH, WS_URL, json=ws_json(errors=errors))
        with pytest.raises(SubmissionError) as info:
            mgr.submit(COLL, {"dc.title": "T"})
        assert info.value.errors == errors
        assert not [c for c in submit_server.calls if c.request.method == "DELETE"]
        assert not [c for c in submit_server.calls if c.request.url.endswith("/workflow/workflowitems")]

    def test_delete_on_error(self, mgr, submit_server):
        with pytest.raises(ValueError):
            mgr.submit(COLL, {"local.unknown": "x"}, delete_on_error=True)
        assert [c for c in submit_server.calls if c.request.method == "DELETE"]
