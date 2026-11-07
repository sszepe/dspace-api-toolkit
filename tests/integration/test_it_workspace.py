"""Workspace items: submission lifecycle, sections, configuration and deposit"""

import pytest

from dspace_api.exceptions import DSpaceError, NotFoundError
from dspace_api.workspaces import SubmissionError

from .helpers import unsupported, unsupported_on

BIBTEX = b"""@article{toolkit2026,
  title   = {Toolkit integration test import},
  author  = {Doe, Jane and Roe, Richard},
  journal = {Journal of Testing},
  year    = {2026}
}
"""


@pytest.fixture
def new_submission(workspace, collection, entity_type, cleanup):
    def factory():
        ws = workspace.start_submission(collection["uuid"], entity_type)
        cleanup(lambda: workspace.delete(ws["id"]))
        return ws
    return factory


def section_of(workspace, ws_id, kind):
    try:
        return workspace.section_name(ws_id, kind)
    except ValueError:
        unsupported(f"the submission definition has no {kind!r} section")


def required_fields(errors):
    """{section: [field]} from error.validation.required paths like /sections/<s>/<field>."""
    result = {}
    for error in errors:
        if error.get("message") != "error.validation.required":
            continue
        for path in error.get("paths") or []:
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "sections" and parts[2].count(".") in (1, 2):
                result.setdefault(parts[1], []).append(parts[2])
    return result


@pytest.mark.feature("workspace.lifecycle")
def test_lifecycle(workspace, collection, me, new_submission):
    ws = new_submission()
    assert workspace.get(ws["id"])["id"] == ws["id"]
    item = workspace.get_item_of(ws["id"])
    assert workspace.get_collection_of(ws["id"])["uuid"] == collection["uuid"]
    assert workspace.find_by_item(item["uuid"])["id"] == ws["id"]
    assert ws["id"] in [w["id"] for w in workspace.find_by_submitter(me["uuid"])]
    assert workspace.get_definition_of(ws["id"]).get("name")

    other = workspace.start_submission(collection["uuid"])
    assert workspace.delete(other["id"]) is True
    with pytest.raises(NotFoundError):
        workspace.get(other["id"])


@pytest.mark.feature("workspace.config")
def test_submission_config(workspace, collection, new_submission):
    definitions = workspace.get_submission_definitions()
    assert definitions
    definition = workspace.get_definition_for_collection(collection["uuid"])
    sections = workspace.get_definition_sections(definition["name"])
    assert sections and all("sectionType" in s for s in sections)

    ws = new_submission()
    forms = [name for name, kind in workspace.section_types(ws["id"]).items() if kind == "submission-form"]
    assert forms
    assert "dc.title" in [f for form in forms for f in workspace.form_fields(form)]
    routed, unroutable = workspace.route_metadata(ws["id"], {"dc.title": "x", "toolkit.never.exists": "y"})
    assert any("dc.title" in fields for fields in routed.values())
    assert unroutable == ["toolkit.never.exists"]


@pytest.mark.feature("workspace.metadata")
def test_fill_metadata(workspace, new_submission, run_id):
    ws = new_submission()
    updated = workspace.fill_metadata(ws["id"], {"dc.title": f"IT submission {run_id}"})
    form = next(s for s, data in updated["sections"].items() if isinstance(data, dict) and "dc.title" in data)
    assert updated["sections"][form]["dc.title"][0]["value"] == f"IT submission {run_id}"
    with pytest.raises(ValueError):
        workspace.fill_metadata(ws["id"], {"toolkit.never.exists": "y"})

    updated = workspace.patch_section_metadata(ws["id"], form, {"dc.title": "Changed"})
    assert updated["sections"][form]["dc.title"][0]["value"] == "Changed"


@pytest.mark.feature("workspace.upload")
def test_upload_and_file_operations(workspace, new_submission):
    ws = new_submission()
    section = section_of(workspace, ws["id"], "upload")
    workspace.upload_file(ws["id"], b"first file", name="first.txt")
    updated = workspace.upload_file(ws["id"], b"second file", name="second.txt")
    files = workspace.uploaded_files(updated)
    assert [f["metadata"]["dc.title"][0]["value"] for _, _, f in files] == ["first.txt", "second.txt"]

    _, index, second = workspace.find_file(updated, "second.txt")
    updated = workspace.patch_file_metadata(ws["id"], index, {"dc.description": "described"}, section=section)
    assert workspace.find_file(updated, "second.txt")[2]["metadata"]["dc.description"][0]["value"] == "described"

    updated = workspace.set_primary_file(ws["id"], second["uuid"], section=section)
    assert updated["sections"][section].get("primary") == second["uuid"]
    updated = workspace.clear_primary_file(ws["id"], section=section)
    assert not updated["sections"][section].get("primary")

    options = workspace.get_submission_upload(section).get("accessConditionOptions") or []
    names = [o.get("name") for o in options]
    if "openaccess" in names:
        updated = workspace.set_file_access_conditions(ws["id"], index, [{"name": "openaccess"}], section=section)
        conditions = workspace.find_file(updated, "second.txt")[2].get("accessConditions") or []
        assert [c.get("name") for c in conditions] == ["openaccess"]

    updated = workspace.remove_file(ws["id"], 0, section=section)
    assert [f["metadata"]["dc.title"][0]["value"] for _, _, f in workspace.uploaded_files(updated)] == ["second.txt"]


@pytest.mark.feature("workspace.license")
def test_grant_license(workspace, new_submission):
    ws = new_submission()
    section = section_of(workspace, ws["id"], "license")
    updated = workspace.grant_license(ws["id"], section=section)
    assert updated["sections"][section]["granted"] is True


@pytest.mark.feature("workspace.access")
def test_item_access_conditions(workspace, new_submission):
    ws = new_submission()
    section = section_of(workspace, ws["id"], "access")
    with unsupported_on(404, what="submissionaccessoptions"):
        options = workspace.get_access_options(section)
    names = [o.get("name") for o in options.get("accessConditionOptions") or []]
    if "openaccess" not in names:
        unsupported(f"access section offers no 'openaccess' option ({names})")
    updated = workspace.set_access_conditions(ws["id"], [{"name": "openaccess"}], section=section)
    assert [c["name"] for c in updated["sections"][section]["accessConditions"]] == ["openaccess"]
    updated = workspace.remove_access_condition(ws["id"], section=section)
    assert not updated["sections"][section].get("accessConditions")


@pytest.mark.feature("workspace.cclicense")
def test_cc_license(workspace, new_submission):
    ws = new_submission()
    section = section_of(workspace, ws["id"], "cclicense")
    licenses = workspace.get_cc_licenses()
    if not licenses:
        unsupported("no CC licenses configured")
    license_id = licenses[0]["id"]
    answers = {f["id"]: f["enums"][0]["id"] for f in licenses[0].get("fields") or [] if f.get("enums")}
    with unsupported_on(404, 500, what="CC license URL lookup (needs creativecommons.org)"):
        uri = workspace.get_cc_license_url(license_id, answers)
    updated = workspace.set_cc_license(ws["id"], uri, section=section)
    assert updated["sections"][section].get("uri") == uri
    workspace.remove_cc_license(ws["id"], section=section)


@pytest.mark.feature("workspace.import_file")
def test_create_from_bibtex(workspace, collection, cleanup):
    with unsupported_on(400, 405, 415, 422, 500, what="workspace item creation from files"):
        created = workspace.create_from_files([(BIBTEX, "toolkit.bib")], collection_uuid=collection["uuid"])
    for ws in created:
        cleanup(lambda ws_id=ws["id"]: workspace.delete(ws_id))
    titles = [v.get("value") for ws in created for data in (ws.get("sections") or {}).values()
              if isinstance(data, dict) for v in data.get("dc.title") or []]
    assert "Toolkit integration test import" in titles


@pytest.mark.feature("workspace.deposit")
def test_submit_and_deposit(workspace, items, collection, entity_type, run_id, cleanup):
    title = f"IT deposit {run_id}"
    result = workspace.submit(collection["uuid"], {"dc.title": title}, files=[(b"deposit", "deposit.txt")],
                              entity_type=entity_type, deposit=False, strict_metadata=False)
    ws_id = result["workspace_item"]["id"]
    item_uuid = result["item_uuid"]
    cleanup(lambda: items.delete_item(item_uuid))
    cleanup(lambda: workspace.delete(ws_id))

    # Fill whatever else this installation's forms require
    missing = required_fields(result["errors"])
    for section, fields in missing.items():
        workspace.patch_section_metadata(ws_id, section, {f: "2026" if "date" in f else "Toolkit IT" for f in fields})

    try:
        workspace.deposit(ws_id)
    except SubmissionError as exc:
        pytest.fail(f"deposit rejected, remaining validation errors: {exc.errors}")
    with pytest.raises(DSpaceError):
        workspace.get(ws_id)  # no longer a workspace item
    item = items.get_item(item_uuid)
    assert item["metadata"]["dc.title"][0]["value"] == title


@pytest.mark.feature("workspace.deposit")
def test_deposit_reports_validation_errors(workspace, collection, entity_type, cleanup):
    ws = workspace.start_submission(collection["uuid"], entity_type)
    cleanup(lambda: workspace.delete(ws["id"]))
    with pytest.raises(SubmissionError) as info:
        workspace.deposit(ws["id"])   # no title, no license
    assert info.value.errors
