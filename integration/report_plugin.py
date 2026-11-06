# -*- coding: UTF-8 -*-
"""
pytest plugin that writes a per-target JSON report for the compatibility matrix.

    pytest tests/integration -p integration.report_plugin \\
        --it-report reports/dspace-9.json --it-target dspace-9

Each test ends up with one outcome:

    passed       the feature works
    failed       an assertion or API call failed
    error        setup/teardown failed (fixture, stack problem)
    unsupported  the server does not offer the feature (test called
                 pytest.skip("unsupported: ..."), see tests/integration/conftest.py)
    skipped      skipped for another reason (missing config, opt-in test)
    xfailed / xpassed   known issue marked with pytest.mark.xfail

Features come from @pytest.mark.feature("<id>") markers.
"""

import json
import os
import platform
import time
from datetime import datetime, timezone

import pytest

UNSUPPORTED_PREFIX = "unsupported:"
# Later phases may only make an outcome worse, never better
_SEVERITY = {"passed": 0, "xpassed": 1, "skipped": 1, "unsupported": 1, "xfailed": 1, "failed": 2, "error": 3}


def pytest_addoption(parser):
    group = parser.getgroup("dspace-integration")
    group.addoption("--it-report", default=None, help="Write the integration JSON report to this path")
    group.addoption("--it-target", default=None, help="Target name recorded in the report")


def _skip_reason(report):
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
    else:
        reason = str(longrepr or "")
    return reason[len("Skipped: "):] if reason.startswith("Skipped: ") else reason


def _short_error(report, limit=600):
    text = getattr(report, "longreprtext", "") or str(report.longrepr or "")
    lines = [line for line in text.strip().splitlines() if line.strip()]
    # The last "E   ..." lines carry the actual error; fall back to the tail
    errors = [line.strip()[1:].strip() for line in lines if line.lstrip().startswith("E ")]
    message = " | ".join(errors[-3:]) if errors else (lines[-1] if lines else "")
    return message[:limit]


def classify(report):
    """(outcome, message) for one phase report."""
    wasxfail = hasattr(report, "wasxfail")
    if report.passed:
        return ("xpassed", report.wasxfail) if wasxfail else ("passed", "")
    if report.skipped:
        if wasxfail:
            return "xfailed", report.wasxfail or ""
        reason = _skip_reason(report)
        if reason.lower().startswith(UNSUPPORTED_PREFIX):
            return "unsupported", reason[len(UNSUPPORTED_PREFIX):].strip()
        return "skipped", reason
    # failed
    if report.when == "call":
        return "failed", _short_error(report)
    return "error", f"{report.when}: {_short_error(report)}"


def server_info(api_url, timeout=10):
    """Name/version from the REST root endpoint, or an error description."""
    if not api_url:
        return {}
    try:
        import requests
        response = requests.get(api_url.rstrip("/"), headers={"Accept": "application/json"}, timeout=timeout)
        data = response.json() if response.ok else {}
        return {key: data.get(key) for key in ("dspaceVersion", "dspaceServer", "dspaceName", "dspaceUI")
                if data.get(key) is not None} or {"error": f"HTTP {response.status_code}"}
    except Exception as exc:  # noqa: BLE001 - informational only
        return {"error": f"{type(exc).__name__}: {exc}"}


class IntegrationReport:
    def __init__(self, path, target):
        self.path = path
        self.target = target
        self.results = {}
        self.started = None
        self.server = {}

    # -- hooks ------------------------------------------------------------

    def pytest_sessionstart(self, session):
        self.started = time.time()
        self.server = server_info(os.getenv("DSPACE_IT_URL"))

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items):
        for item in items:
            features = []
            for marker in item.iter_markers("feature"):
                features.extend(marker.args)
            item.user_properties.append(("features", sorted(set(features))))

    def pytest_runtest_logreport(self, report):
        outcome, message = classify(report)
        features = dict(report.user_properties).get("features", [])
        entry = self.results.setdefault(report.nodeid, {
            "nodeid": report.nodeid, "features": features, "outcome": None,
            "message": "", "duration": 0.0,
        })
        entry["duration"] = round(entry["duration"] + getattr(report, "duration", 0.0), 3)
        if report.when == "teardown" and outcome == "passed":
            return
        if entry["outcome"] is None or _SEVERITY[outcome] > _SEVERITY[entry["outcome"]]:
            entry["outcome"], entry["message"] = outcome, message

    def pytest_sessionfinish(self, session, exitstatus):
        if not self.path:
            return
        results = sorted(self.results.values(), key=lambda r: r["nodeid"])
        summary = {}
        for result in results:
            summary[result["outcome"]] = summary.get(result["outcome"], 0) + 1
        report = {
            "schema": 1,
            "target": {
                "name": self.target or os.getenv("DSPACE_IT_TARGET") or "unnamed",
                "label": os.getenv("DSPACE_IT_LABEL") or "",
                "api_url": os.getenv("DSPACE_IT_URL") or "",
                "server": self.server,
            },
            "run": {
                "started": datetime.fromtimestamp(self.started or time.time(), timezone.utc).isoformat(),
                "finished": datetime.now(timezone.utc).isoformat(),
                "duration": round(time.time() - (self.started or time.time()), 1),
                "exitstatus": int(exitstatus),
                "python": platform.python_version(),
            },
            "summary": summary,
            "results": results,
        }
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)


def pytest_configure(config):
    config.addinivalue_line("markers", "feature(*ids): toolkit capability exercised by an integration test")
    path = config.getoption("--it-report")
    config.pluginmanager.register(IntegrationReport(path, config.getoption("--it-target")), "dspace-it-report")
