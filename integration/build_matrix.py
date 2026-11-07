# -*- coding: UTF-8 -*-
"""
Combine per-target integration reports into a compatibility matrix.

    python -m integration.build_matrix --reports reports
    python -m integration.build_matrix --reports reports --baseline last-week/matrix.json

Writes matrix.md, matrix.html and matrix.json into --out (default: --reports).
With --baseline, cells that were "pass" before and are now "fail" are flagged
as regressions (and listed), and the exit code is 1 if there are any.

Cell states (one feature on one target):
    pass         every test for the feature passed
    fail         at least one test failed or errored
    partial      some passed, others were skipped/unsupported
    unsupported  the server does not offer the feature
    skipped      not run (opt-in, missing config)
    n/a          the target produced no result for the feature
"""

import argparse
import glob
import html
import json
import os
import sys
from datetime import datetime, timezone

from .features import FEATURES, area

SYMBOLS = {"pass": "✅", "fail": "❌", "partial": "🟡", "unsupported": "⛔", "skipped": "⏭", "n/a": "·"}
LABELS = {"pass": "pass", "fail": "fail", "partial": "partial", "unsupported": "not supported by server",
          "skipped": "skipped", "n/a": "no result"}
COLORS = {"pass": "#d9f2df", "fail": "#f8d7da", "partial": "#fff3cd", "unsupported": "#e2e3e5",
          "skipped": "#f1f3f5", "n/a": "#ffffff"}
UNTAGGED = "(untagged)"


# ---------------------------------------------------------------------------
# Loading and aggregation
# ---------------------------------------------------------------------------

def load_reports(directory, order=None):
    """Reports from directory/*.json (excluding matrix.json), ordered by `order` (target names)."""
    reports = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        if os.path.basename(path) == "matrix.json":
            continue
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "target" in data and "results" in data:
            reports.append(data)
    if order:
        rank = {name: i for i, name in enumerate(order)}
        reports.sort(key=lambda r: (rank.get(r["target"]["name"], len(rank)), r["target"]["name"]))
    return reports


def cell_state(outcomes):
    if not outcomes:
        return "n/a"
    if any(o in ("failed", "error") for o in outcomes):
        return "fail"
    good = sum(o in ("passed", "xpassed", "xfailed") for o in outcomes)
    if good == len(outcomes):
        return "pass"
    if good:
        return "partial"
    if all(o == "unsupported" for o in outcomes):
        return "unsupported"
    return "unsupported" if "unsupported" in outcomes else "skipped"


def build(reports, baseline=None):
    """Matrix data: targets, rows (feature -> target -> cell), regressions."""
    targets = [r["target"] for r in reports]
    names = [t["name"] for t in targets]
    seen = []
    for report in reports:
        for result in report["results"]:
            for feature in result.get("features") or [UNTAGGED]:
                if feature not in seen:
                    seen.append(feature)
    catalog_order = list(FEATURES)
    features = sorted(seen, key=lambda f: (catalog_order.index(f) if f in FEATURES else len(catalog_order), f))

    rows = {}
    for feature in features:
        rows[feature] = {}
        for report in reports:
            tests = [r for r in report["results"] if feature in (r.get("features") or [UNTAGGED])]
            rows[feature][report["target"]["name"]] = {
                "state": cell_state([t["outcome"] for t in tests]),
                "tests": [{k: t.get(k) for k in ("nodeid", "outcome", "message")} for t in tests],
            }
        for report in reports:
            if report.get("setup_error"):
                rows[feature][report["target"]["name"]] = {"state": "fail", "tests": [],
                                                           "setup_error": report["setup_error"]}

    regressions = []
    if baseline:
        before = baseline.get("rows", {})
        for feature, cells in rows.items():
            for name, cell in cells.items():
                old = before.get(feature, {}).get(name, {}).get("state")
                if old == "pass" and cell["state"] == "fail":
                    cell["regression"] = True
                    regressions.append({"feature": feature, "target": name})

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "targets": targets,
        "target_names": names,
        "summaries": {r["target"]["name"]: {"summary": r.get("summary", {}), "run": r.get("run", {}),
                                            "setup_error": r.get("setup_error")} for r in reports},
        "features": features,
        "rows": rows,
        "regressions": regressions,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _version(target):
    server = target.get("server") or {}
    return server.get("dspaceVersion") or server.get("error") or "unknown"


def _cell_text(cell):
    return SYMBOLS[cell["state"]] + (" 🔻" if cell.get("regression") else "")


def _problems(cell):
    if cell.get("setup_error"):
        return [f"stack/setup: {cell['setup_error']}"]
    return [f"{t['nodeid'].split('::')[-1]}: {t['outcome']} - {t['message']}"
            for t in cell["tests"] if t["outcome"] in ("failed", "error", "unsupported") and t.get("message")]


def render_markdown(matrix):
    names = matrix["target_names"]
    lines = ["# DSpace toolkit compatibility matrix", "",
             f"Generated {matrix['generated']}", "",
             "Legend: " + " · ".join(f"{SYMBOLS[s]} {LABELS[s]}" for s in SYMBOLS) + " · 🔻 regression", ""]
    lines.append("| Target | Server version | " + " | ".join(
        ["passed", "failed", "error", "unsupported", "skipped"]) + " | duration |")
    lines.append("|---|---|" + "---|" * 6)
    for target in matrix["targets"]:
        info = matrix["summaries"][target["name"]]
        counts = info["summary"]
        status = f" ⚠ {info['setup_error']}" if info.get("setup_error") else ""
        lines.append(f"| **{target['name']}** {target.get('label') or ''} | {_version(target)}{status} | "
                     + " | ".join(str(counts.get(k, 0)) for k in ("passed", "failed", "error", "unsupported", "skipped"))
                     + f" | {info['run'].get('duration', '-')}s |")
    lines += ["", "| Area | Feature | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    current_area = None
    for feature in matrix["features"]:
        feature_area = area(feature) if feature != UNTAGGED else UNTAGGED
        shown_area = feature_area if feature_area != current_area else ""
        current_area = feature_area
        description = FEATURES.get(feature, "")
        label = f"`{feature}`" + (f"<br><sub>{description}</sub>" if description else "")
        cells = [_cell_text(matrix["rows"][feature][n]) for n in names]
        lines.append(f"| {shown_area} | {label} | " + " | ".join(cells) + " |")

    if matrix["regressions"]:
        lines += ["", "## Regressions", ""]
        lines += [f"- 🔻 `{r['feature']}` on **{r['target']}**" for r in matrix["regressions"]]

    details = []
    for feature in matrix["features"]:
        for name in names:
            for problem in _problems(matrix["rows"][feature][name]):
                details.append(f"- **{name}** · `{feature}` · {problem}")
    if details:
        lines += ["", "## Failures and unsupported features", ""] + details
    return "\n".join(lines) + "\n"


def render_html(matrix):
    names = matrix["target_names"]
    esc = html.escape
    head = "".join(
        f"<th>{esc(t['name'])}<br><small>{esc(t.get('label') or '')}</small><br>"
        f"<small class=v>{esc(_version(t))}</small></th>" for t in matrix["targets"])
    body = []
    current_area = None
    for feature in matrix["features"]:
        feature_area = area(feature) if feature != UNTAGGED else UNTAGGED
        if feature_area != current_area:
            body.append(f"<tr class=area><td colspan={len(names) + 1}>{esc(feature_area)}</td></tr>")
            current_area = feature_area
        cells = []
        for name in names:
            cell = matrix["rows"][feature][name]
            tip = "\n".join(_problems(cell)) or LABELS[cell["state"]]
            cells.append(f"<td style='background:{COLORS[cell['state']]}' title='{esc(tip, quote=True)}'>"
                         f"{_cell_text(cell)}</td>")
        body.append(f"<tr><th class=f><code>{esc(feature)}</code><br>"
                    f"<small>{esc(FEATURES.get(feature, ''))}</small></th>{''.join(cells)}</tr>")
    legend = " ".join(f"<span class=l style='background:{COLORS[s]}'>{SYMBOLS[s]} {LABELS[s]}</span>"
                      for s in SYMBOLS)
    regressions = "".join(f"<li>🔻 <code>{esc(r['feature'])}</code> on <b>{esc(r['target'])}</b></li>"
                          for r in matrix["regressions"])
    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>DSpace toolkit compatibility matrix</title>
<style>
body{{font:14px/1.4 system-ui,sans-serif;margin:24px;color:#1f2328;background:#fff}}
table{{border-collapse:collapse}} td,th{{border:1px solid #d0d7de;padding:6px 10px;text-align:center}}
th.f{{text-align:left;font-weight:normal}} tr.area td{{background:#f6f8fa;font-weight:600;text-align:left}}
.v{{color:#57606a}} .l{{display:inline-block;padding:2px 8px;margin:2px;border-radius:4px}}
td[title]{{cursor:help}}
</style></head><body>
<h1>DSpace toolkit compatibility matrix</h1>
<p>Generated {esc(matrix['generated'])}. Hover a cell for details.</p><p>{legend}</p>
{'<h2>Regressions</h2><ul>' + regressions + '</ul>' if regressions else ''}
<table><thead><tr><th>Feature</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>
</body></html>
"""


def write_matrix(reports_dir, out_dir=None, baseline_path=None, order=None):
    out_dir = out_dir or reports_dir
    baseline = None
    if baseline_path:
        with open(baseline_path, encoding="utf-8") as fh:
            baseline = json.load(fh)
    matrix = build(load_reports(reports_dir, order), baseline)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "matrix.json"), "w", encoding="utf-8") as fh:
        json.dump(matrix, fh, indent=2)
    with open(os.path.join(out_dir, "matrix.md"), "w", encoding="utf-8") as fh:
        fh.write(render_markdown(matrix))
    with open(os.path.join(out_dir, "matrix.html"), "w", encoding="utf-8") as fh:
        fh.write(render_html(matrix))
    return matrix


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the compatibility matrix from integration reports")
    parser.add_argument("--reports", default="reports", help="Directory with <target>.json reports")
    parser.add_argument("--out", help="Output directory (default: --reports)")
    parser.add_argument("--baseline", help="Previous matrix.json to detect regressions")
    args = parser.parse_args(argv)
    order = None
    try:
        from .run_matrix import load_targets
        order = [t["name"] for t in load_targets()]
    except Exception:  # noqa: BLE001 - ordering is cosmetic
        pass
    matrix = write_matrix(args.reports, args.out, args.baseline, order)
    print(f"Matrix: {len(matrix['features'])} features x {len(matrix['target_names'])} targets "
          f"-> {os.path.join(args.out or args.reports, 'matrix.md')}")
    if matrix["regressions"]:
        print(f"{len(matrix['regressions'])} regression(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
