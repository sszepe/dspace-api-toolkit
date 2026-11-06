# -*- coding: UTF-8 -*-
"""
Measure which DSpace RestContract endpoints the toolkit uses.

    git clone --depth 1 https://github.com/DSpace/RestContract /tmp/RestContract
    DSPACE_RECORD_CALLS=/tmp/calls.txt pytest -o addopts= -q        # record every request the toolkit sends
    python tools/contract_coverage.py --contract /tmp/RestContract --calls /tmp/calls.txt \
        --update docs/REST_CONTRACT_COVERAGE.md

How it works
  - Contract side: every bold endpoint heading in the contract's *.md files,
    e.g. "**GET /api/core/items/<:uuid>/bundles**" (no method = GET). Headings
    followed by "_Unsupported._" are endpoints the server rejects (405) and do
    not count. Path parameters (<:uuid>, {id}, ...) become wildcards; query
    strings are ignored, so search methods are matched by their path.
    Files with a "Patch operations" section add PATCH on their single-object
    endpoint (metadata patch is documented that way).
  - Toolkit side: the unit tests reach 100% of the code, so the requests they
    send (recorded by tests/conftest.py) are the toolkit's endpoint inventory.
  - An endpoint is "used" when a recorded request has the same method and
    matches the template segment by segment.

This is a measurement of request coverage, not of feature completeness: an
endpoint can be used without every parameter or option being exposed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict
from urllib.parse import urlparse

HEADING = re.compile(r"\*\*(?:(GET|POST|PUT|PATCH|DELETE)\s+)?(/api/[^*\s]+)\s*\*\*")
# "POST /api/core/items" or "`PATCH /api/...`" in running text
INLINE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+`?(?:https?://\S+?)?(/api/[^\s`*\"')\]]+)")
# curl examples: curl ... -X POST ... https://host/server/api/...
CURL = re.compile(r"curl\b.*?-X\s*['\"]?(GET|POST|PUT|PATCH|DELETE)\b.*?https?://[^\s'\"]*?(/api/[^\s'\"?]+)")
CURL_URL_FIRST = re.compile(r"curl\b[^\n]*?https?://[^\s'\"]*?(/api/[^\s'\"?]+)[^\n]*?-X\s*['\"]?(GET|POST|PUT|PATCH|DELETE)\b")
PARAM = re.compile(r"^(<[^>]*>|\{[^}]*\}|:[a-z_-]+|<[^>]*>\.[a-z]+)$", re.I)
API_PREFIX = re.compile(r"^.*?/api(?=/)")
SECTION = re.compile(r"^(#{1,6})\s+(.*)$")

# Operations the contract describes only in prose (by analogy or as linked
# entities without a path heading). (method, path, contract file, where)
DOCUMENTED_IN_PROSE = [
    *[(m, f"/core/collections/{{}}/{role}", "collections.md", "role groups: 'same as adminGroup'")
      for role in ("submittersGroup", "itemReadGroup", "bitstreamReadGroup") for m in ("POST", "DELETE")],
    ("POST", "/core/collections/{}/workflowGroups/{}", "collections.md", "workflow groups: 'same as adminGroup'"),
    ("GET", "/versioning/versions/{}/item", "versions.md", "linked entity 'Item'"),
    ("GET", "/versioning/versions/{}/versionhistory", "versions.md", "linked entity 'Version History'"),
    ("GET", "/submission/vocabularyEntryDetails/{}/parent", "vocabularyEntryDetails.md", "exposed link 'parent'"),
    ("GET", "/submission/vocabularyEntryDetails/{}/children", "vocabularyEntryDetails.md",
     "exposed link 'children'"),
    ("POST", "/workflow/claimedtasks/{}", "claimedtasks.md", "'POST Method (single resource level)' (perform)"),
    ("DELETE", "/workflow/claimedtasks/{}", "claimedtasks.md", "'DELETE Method' (unclaim)"),
    ("GET", "/core/relationships/{}/leftItem", "relationships.md", "HAL link 'leftItem'"),
    ("GET", "/core/relationships/{}/rightItem", "relationships.md", "HAL link 'rightItem'"),
    ("GET", "/core/relationshiptypes/{}/leftType", "relationshiptypes.md", "HAL link 'leftType'"),
    ("GET", "/core/relationshiptypes/{}/rightType", "relationshiptypes.md", "HAL link 'rightType'"),
    ("POST", "/submission/workspaceitems", "workspaceitems.md", "'Multipart POST Method' (create, import)"),
    ("POST", "/submission/workspaceitems/{}", "workspaceitems.md", "'Multipart POST Method on a single workspaceitem' (upload)"),
    ("PATCH", "/submission/workspaceitems/{}", "submission.md", "section PATCH (workspaceitem-data-*.md)"),
    ("PATCH", "/workflow/workflowitems/{}", "submission.md", "section PATCH (workspaceitem-data-*.md)"),
]


def section_method_of(title):
    """Method implied by a section title: '## DELETE Method', '## Creating a collection', ..."""
    explicit = re.search(r"\b(GET|POST|PUT|PATCH|DELETE)\b", title)
    if explicit:
        return explicit.group(1)
    lowered = title.lower()
    for word, method in (("delet", "DELETE"), ("remov", "DELETE"), ("creat", "POST"), ("upload", "POST")):
        if lowered.startswith(word) or f" {word}" in lowered:
            return method
    return None


START, END = "<!-- contract-coverage:start -->", "<!-- contract-coverage:end -->"


def normalize_template(path):
    """'/api/core/items/<:uuid>/bundles?x=1' -> ('core', 'items', '{}', 'bundles')"""
    path = re.sub(r"/\[[^\]/]*\]", "/{}", path)            # curl examples: /bundles/[uuid]/...
    path = path.split("?", 1)[0].split("[", 1)[0].split("#", 1)[0].rstrip("/.,;:)")
    path = path[len("/api"):] if path.startswith("/api") else path
    segments = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        concrete_id = re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}|\d+", segment, re.I)
        segments.append("{}" if PARAM.match(segment) or "<" in segment or "{" in segment or concrete_id
                        else segment)
    return tuple(segments)


def parse_contract(directory):
    """[{method, template, path, file, unsupported}] in file order, de-duplicated."""
    endpoints = OrderedDict()
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md") or name in ("README.md", "endpoints.md"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        single = None
        section_method = None
        for i, line in enumerate(lines):
            heading = SECTION.match(line)
            if heading:
                section_method = section_method_of(heading.group(2)) if len(heading.group(1)) <= 4 else section_method
            for method, path in HEADING.findall(line):
                template = normalize_template(path)
                if not template:
                    continue
                method = method or section_method or "GET"
                following = " ".join(lines[i + 1:i + 4]).lower()
                unsupported = ("unsupported" in following[:80] or "not implemented" in following
                               or following.lstrip().startswith("not allowed")
                               or "405 method not allowed" in following[:200])
                key = (method, template)
                if key not in endpoints:
                    endpoints[key] = {"method": method, "template": template, "path": "/" + "/".join(template),
                                      "file": name, "unsupported": unsupported}
                elif not unsupported:
                    endpoints[key]["unsupported"] = False
                if single is None and len(template) == 3 and template[-1] == "{}":
                    single = template
        # endpoints that appear only in running text or curl examples
        for line in lines:
            found = INLINE.findall(line) + CURL.findall(line) + [(m, p) for p, m in CURL_URL_FIRST.findall(line)]
            for method, path in found:
                template = normalize_template(path)
                if template and (method, template) not in endpoints:
                    # "_Unsupported._ ... please use DELETE /api/..." lines name alternatives, not endpoints
                    if "unsupported" in line.lower() or "/rest/api/" in line:
                        continue
                    endpoints[(method, template)] = {"method": method, "template": template,
                                                     "path": "/" + "/".join(template), "file": name,
                                                     "unsupported": False, "inline": True}
        text = "\n".join(lines).lower()
        if single and ("## patch operations" in text or "## patch" in text or "### patch" in text):
            endpoints.setdefault(("PATCH", single), {"method": "PATCH", "template": single,
                                                     "path": "/" + "/".join(single), "file": name,
                                                     "unsupported": False})
    for method, path, name, where in DOCUMENTED_IN_PROSE:
        template = normalize_template(path)
        endpoints.setdefault((method, template), {"method": method, "template": template, "path": path,
                                                  "file": name, "unsupported": False, "prose": where})
    return list(endpoints.values())


def parse_calls(path):
    """{(METHOD, segments)} from the recorded 'METHOD URL' lines."""
    calls = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            method, _, url = line.strip().partition(" ")
            if not url:
                continue
            route = API_PREFIX.sub("", urlparse(url).path)
            calls.add((method.upper(), tuple(s for s in route.strip("/").split("/") if s)))
    return calls


def matches(template, segments):
    return len(template) == len(segments) and all(t == "{}" or t == s for t, s in zip(template, segments))


def analyse(endpoints, calls):
    categories = {e["template"][0] for e in endpoints} | {"security", "authn"}
    calls = {c for c in calls if c[1] and c[1][0] in categories and len(c[1]) >= 2}
    used_calls = set()
    for endpoint in endpoints:
        hits = {c for c in calls if c[0] == endpoint["method"] and matches(endpoint["template"], c[1])}
        endpoint["used"] = bool(hits)
        used_calls |= hits
    # A call matched only by a wildcard template although a more specific literal
    # exists elsewhere is still fine; calls matching nothing are toolkit-only.
    extra = sorted({(m, "/" + "/".join(s)) for m, s in calls - used_calls})
    return endpoints, extra


def generalize(path):
    """Replace ids in a concrete path for display: /core/items/1234-.../bundles -> /core/items/{}/bundles"""
    out = []
    for segment in path.strip("/").split("/"):
        looks_like_id = (bool(re.search(r"\d", segment)) and not re.fullmatch(r"[a-zA-Z]+\d?", segment)) or \
            bool(re.fullmatch(r"[a-z0-9]{8}(-[a-z0-9]{4}){3}-[a-z0-9]{12}", segment)) or "-uuid" in segment
        out.append("{}" if looks_like_id or segment in ("x", "h", "c", "bs", "b1", "abc-123", "old", "new")
                   else segment)
    return "/" + "/".join(out)


def summarize(endpoints):
    files = OrderedDict()
    for endpoint in endpoints:
        stats = files.setdefault(endpoint["file"], {"total": 0, "used": 0, "unsupported": 0, "missing": []})
        if endpoint["unsupported"]:
            stats["unsupported"] += 1
            continue
        stats["total"] += 1
        if endpoint["used"]:
            stats["used"] += 1
        else:
            stats["missing"].append(f"{endpoint['method']} {endpoint['path']}")
    return files


def render(files, extra, contract_rev):
    total = sum(f["total"] for f in files.values())
    used = sum(f["used"] for f in files.values())
    lines = [START, "",
             f"_Generated by `tools/contract_coverage.py` from RestContract {contract_rev}. "
             f"Do not edit between the markers._", "",
             f"**{used} of {total} documented endpoints used ({used * 100 // max(total, 1)}%)** "
             f"across {len(files)} contract files; endpoints the contract marks as unsupported are not counted.",
             "", "| Contract file | Used | Not used |", "|---|---|---|"]
    for name, stats in sorted(files.items(), key=lambda kv: (-(kv[1]["used"] / max(kv[1]["total"], 1)), kv[0])):
        if not stats["total"]:
            continue
        missing = "<br>".join(f"`{m}`" for m in stats["missing"]) or "-"
        lines.append(f"| [{name}](https://github.com/DSpace/RestContract/blob/main/{name}) | "
                     f"{stats['used']}/{stats['total']} | {missing} |")
    shown = sorted({(m, generalize(p)) for m, p in extra})
    if shown:
        lines += ["", "Requests the toolkit sends that match no contract heading "
                      "(DSpace-CRIS extensions, undocumented variants, or contract gaps):", ""]
        lines += [f"- `{m} {p}`" for m, p in shown]
    lines += ["", END]
    return "\n".join(lines)


def contract_revision(directory):
    try:
        return subprocess.run(["git", "-C", directory, "log", "-1", "--format=%h (%cs)"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "(unknown revision)"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--contract", required=True, help="Checkout of https://github.com/DSpace/RestContract")
    parser.add_argument("--calls", required=True, help="File written with DSPACE_RECORD_CALLS")
    parser.add_argument("--update", help="Markdown file whose generated section is replaced")
    parser.add_argument("--json", help="Write the full endpoint list as JSON")
    args = parser.parse_args(argv)

    endpoints, extra = analyse(parse_contract(args.contract), parse_calls(args.calls))
    files = summarize(endpoints)
    section = render(files, extra, contract_revision(args.contract))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"endpoints": [dict(e, template=list(e["template"])) for e in endpoints],
                       "toolkit_only": extra}, fh, indent=2)
    if args.update:
        with open(args.update, encoding="utf-8") as fh:
            text = fh.read()
        if START not in text or END not in text:
            raise SystemExit(f"{args.update} has no {START} ... {END} section")
        before, rest = text.split(START, 1)
        after = rest.split(END, 1)[1]
        with open(args.update, "w", encoding="utf-8") as fh:
            fh.write(before + section + after)
        print(f"updated {args.update}")
    else:
        print(section)
    return 0


if __name__ == "__main__":
    sys.exit(main())
