"""
Tests for tools/contract_coverage.py (the RestContract coverage report).
"""

import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "contract_coverage.py"
spec = importlib.util.spec_from_file_location("contract_coverage", TOOL)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


CONTRACT = {
    "things.md": """# Things
## Main Endpoint
**/api/core/things**

_Unsupported._ Use the search methods.

## Single Thing
**/api/core/things/<:uuid>**

### Search methods
**/api/core/things/search/byName?name=<:name>[&exact=<:bool>]**

**POST /api/core/things/<:uuid>/parts**

curl -i -X PUT "https://demo.dspace.org/api/core/things/[uuid]/owner" -d "x"

_Unsupported._ Please use `DELETE /api/core/old/<:uuid>` instead.

## DELETE Method
**/api/core/things/<:uuid>**

## Patch operations
Metadata can be modified.
""",
    "README.md": "**GET /api/ignored**",
}


@pytest.fixture
def contract(tmp_path):
    for name, text in CONTRACT.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def by_key(endpoints):
    return {(e["method"], e["path"]): e for e in endpoints}


def test_normalize_template():
    assert cc.normalize_template("/api/core/items/<:uuid>/bundles?x=1") == ("core", "items", "{}", "bundles")
    assert cc.normalize_template("/api/core/items/{id}") == ("core", "items", "{}")
    assert cc.normalize_template("/api/core/bundles/[uuid]/primaryBitstream") == ("core", "bundles", "{}",
                                                                                  "primaryBitstream")
    assert cc.normalize_template("/api/core/items/1911e8a4-6939-490c-b58b-a5d70f8d91fb/version") == (
        "core", "items", "{}", "version")
    assert cc.normalize_template("/api/x/search/top[?a=b]") == ("x", "search", "top")


def test_parse_contract(contract):
    endpoints = by_key(cc.parse_contract(str(contract)))
    assert endpoints[("GET", "/core/things")]["unsupported"] is True
    assert endpoints[("GET", "/core/things/{}")]["unsupported"] is False
    assert ("GET", "/core/things/search/byName") in endpoints
    assert ("POST", "/core/things/{}/parts") in endpoints
    assert ("PUT", "/core/things/{}/owner") in endpoints              # from the curl example
    assert ("DELETE", "/core/things/{}") in endpoints                 # "## DELETE Method" section
    assert ("PATCH", "/core/things/{}") in endpoints                  # "Patch operations" section
    assert ("DELETE", "/core/old/{}") not in endpoints                # alternative named on an Unsupported line
    assert not any(path == "/ignored" for _, path in endpoints)       # README.md skipped
    assert ("POST", "/submission/workspaceitems") in endpoints        # documented-in-prose list


def test_section_method():
    assert cc.section_method_of("DELETE Method") == "DELETE"
    assert cc.section_method_of("Creating a collection") == "POST"
    assert cc.section_method_of("Deleting an item") == "DELETE"
    assert cc.section_method_of("Linked entities") is None


def test_analyse_and_render(contract, tmp_path):
    calls = tmp_path / "calls.txt"
    calls.write_text("\n".join([
        "GET http://localhost:8080/server/api/core/things/abc",
        "GET http://localhost:8080/server/api/core/things/search/byName?name=x",
        "PATCH http://localhost:8080/server/api/core/things/abc",
        "GET http://localhost:8080/server/api/core/sites",            # not in this contract
        "GET http://localhost:8080/server/api/x",                     # placeholder URL, ignored
        "",
    ]))
    endpoints, extra = cc.analyse(cc.parse_contract(str(contract)), cc.parse_calls(str(calls)))
    keyed = by_key(endpoints)
    assert keyed[("GET", "/core/things/{}")]["used"] and keyed[("PATCH", "/core/things/{}")]["used"]
    assert not keyed[("DELETE", "/core/things/{}")]["used"]
    assert extra == [("GET", "/core/sites")]

    files = cc.summarize(endpoints)
    assert files["things.md"]["used"] == 3 and "DELETE /core/things/{}" in files["things.md"]["missing"]
    section = cc.render(files, extra, "abc1234 (2026-10-07)")
    assert section.startswith(cc.START) and section.endswith(cc.END)
    assert "RestContract abc1234" in section and "`GET /core/sites`" in section


def test_generalize():
    assert cc.generalize("/core/items/1234/bundles") == "/core/items/{}/bundles"
    assert cc.generalize("/core/collections/cccccccc-cccc-cccc-cccc-cccccccccccc/items") == "/core/collections/{}/items"
    assert cc.generalize("/core/sites") == "/core/sites"


def test_main_updates_markers(contract, tmp_path, capsys):
    calls = tmp_path / "calls.txt"
    calls.write_text("GET http://h/server/api/core/things/1\n")
    doc = tmp_path / "doc.md"
    doc.write_text(f"intro\n{cc.START}\nold\n{cc.END}\noutro\n")
    out_json = tmp_path / "cov.json"
    assert cc.main(["--contract", str(contract), "--calls", str(calls), "--update", str(doc),
                    "--json", str(out_json)]) == 0
    text = doc.read_text()
    assert text.startswith("intro\n") and text.endswith("outro\n") and "old" not in text
    assert json.loads(out_json.read_text())["endpoints"]
    cc.main(["--contract", str(contract), "--calls", str(calls)])
    assert cc.START in capsys.readouterr().out
    doc.write_text("no markers")
    with pytest.raises(SystemExit):
        cc.main(["--contract", str(contract), "--calls", str(calls), "--update", str(doc)])
