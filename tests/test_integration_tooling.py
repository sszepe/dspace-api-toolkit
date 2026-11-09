"""
Unit tests for the integration tooling (integration/): report plugin,
matrix builder and stack runner. No Docker or DSpace server needed.
"""

import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import yaml

from integration import build_matrix as bm
from integration import run_matrix as rm
from integration.features import FEATURES, area
from integration.report_plugin import server_info

pytest_plugins = ["pytester"]


# ---------------------------------------------------------------------------
# Report plugin
# ---------------------------------------------------------------------------

SAMPLE_TESTS = '''
import pytest

@pytest.fixture
def broken():
    raise RuntimeError("fixture exploded")

@pytest.fixture
def bad_teardown():
    yield
    raise RuntimeError("teardown exploded")

@pytest.mark.feature("items.crud")
def test_pass():
    assert True

@pytest.mark.feature("items.crud", "items.upsert")
def test_fail():
    assert 1 == 2, "values differ"

@pytest.mark.feature("bitstreams.primary")
def test_unsupported():
    pytest.skip("unsupported: primaryBitstream answered HTTP 404")

@pytest.mark.feature("site.terms_of_use")
def test_plain_skip():
    pytest.skip("opt-in test")

@pytest.mark.feature("auth.login")
@pytest.mark.xfail(reason="known bug")
def test_xfail():
    assert False

@pytest.mark.feature("auth.login")
@pytest.mark.xfail(reason="fixed meanwhile")
def test_xpass():
    assert True

@pytest.mark.feature("groups.crud")
def test_setup_error(broken):
    pass

@pytest.mark.feature("groups.members")
def test_teardown_error(bad_teardown):
    pass

def test_untagged():
    pass
'''


@pytest.fixture
def run_sample(pytester, tmp_path, monkeypatch):
    monkeypatch.delenv("DSPACE_IT_URL", raising=False)

    def run():
        pytester.makepyfile(test_sample=SAMPLE_TESTS)
        out = tmp_path / "reports" / "t1.json"
        result = pytester.runpytest("-p", "integration.report_plugin", "--it-report", str(out),
                                    "--it-target", "t1")
        return result, json.loads(out.read_text())
    return run


class TestReportPlugin:
    def test_outcomes_features_and_summary(self, run_sample, monkeypatch):
        monkeypatch.setenv("DSPACE_IT_LABEL", "Label 1")
        _, report = run_sample()
        by_name = {r["nodeid"].split("::")[-1]: r for r in report["results"]}
        assert {n: r["outcome"] for n, r in by_name.items()} == {
            "test_pass": "passed", "test_fail": "failed", "test_unsupported": "unsupported",
            "test_plain_skip": "skipped", "test_xfail": "xfailed", "test_xpass": "xpassed",
            "test_setup_error": "error", "test_teardown_error": "error", "test_untagged": "passed"}
        assert by_name["test_fail"]["features"] == ["items.crud", "items.upsert"]
        assert "values differ" in by_name["test_fail"]["message"]
        assert by_name["test_unsupported"]["message"] == "primaryBitstream answered HTTP 404"
        assert by_name["test_setup_error"]["message"].startswith("setup:")
        assert by_name["test_teardown_error"]["message"].startswith("teardown:")
        assert by_name["test_untagged"]["features"] == []
        assert report["target"]["name"] == "t1" and report["target"]["label"] == "Label 1"
        assert report["summary"]["passed"] == 2 and report["summary"]["error"] == 2
        assert report["run"]["exitstatus"] == 1

    def test_no_report_path_writes_nothing(self, pytester):
        pytester.makepyfile(test_x="def test_a():\n    pass\n")
        result = pytester.runpytest("-p", "integration.report_plugin")
        result.assert_outcomes(passed=1)

    def test_server_info(self, monkeypatch):
        assert server_info(None) == {}
        ok = Mock(ok=True, status_code=200, json=Mock(return_value={"dspaceVersion": "DSpace 9.1", "type": "root"}))
        monkeypatch.setattr(requests, "get", Mock(return_value=ok))
        assert server_info("http://x/api") == {"dspaceVersion": "DSpace 9.1"}
        monkeypatch.setattr(requests, "get", Mock(return_value=Mock(ok=False, status_code=503)))
        assert server_info("http://x/api") == {"error": "HTTP 503"}
        monkeypatch.setattr(requests, "get", Mock(side_effect=requests.ConnectionError("down")))
        assert server_info("http://x/api")["error"].startswith("ConnectionError")


# ---------------------------------------------------------------------------
# Matrix builder
# ---------------------------------------------------------------------------

def report(name, results, version="DSpace 9.1", setup_error=None):
    data = {"schema": 1, "target": {"name": name, "label": name.upper(), "server": {"dspaceVersion": version}},
            "run": {"duration": 12.3}, "summary": {}, "results": [
                {"nodeid": f"t.py::{feature}_{i}", "features": [feature] if feature else [],
                 "outcome": outcome, "message": message}
                for i, (feature, outcome, message) in enumerate(results)]}
    for r in data["results"]:
        data["summary"][r["outcome"]] = data["summary"].get(r["outcome"], 0) + 1
    if setup_error:
        data["setup_error"] = setup_error
    return data


def write_reports(directory, *reports):
    directory.mkdir(parents=True, exist_ok=True)
    for r in reports:
        (directory / f"{r['target']['name']}.json").write_text(json.dumps(r))
    (directory / "notes.json").write_text(json.dumps({"other": 1}))     # ignored


class TestMatrix:
    @pytest.mark.parametrize("outcomes,state", [
        ([], "n/a"), (["passed", "passed"], "pass"), (["passed", "failed"], "fail"), (["error"], "fail"),
        (["passed", "unsupported"], "partial"), (["passed", "skipped"], "partial"),
        (["unsupported", "unsupported"], "unsupported"), (["skipped", "unsupported"], "unsupported"),
        (["skipped"], "skipped"), (["xfailed", "xpassed"], "pass"),
    ])
    def test_cell_state(self, outcomes, state):
        assert bm.cell_state(outcomes) == state

    def test_build_and_render(self, tmp_path):
        a = report("dspace-9", [("items.crud", "passed", ""), ("bitstreams.primary", "unsupported", "HTTP 404"),
                                (None, "passed", "")])
        b = report("cris-2025.02", [("items.crud", "failed", "assert 1 == 2"),
                                    ("bitstreams.primary", "passed", "")], version="DSpace-CRIS 2025.02")
        c = report("dspace-10", [], version=None, setup_error="REST API not ready after 1200s")
        write_reports(tmp_path, a, b, c)
        matrix = bm.write_matrix(str(tmp_path), order=["dspace-9", "dspace-10", "cris-2025.02"])

        assert matrix["target_names"] == ["dspace-9", "dspace-10", "cris-2025.02"]
        assert matrix["features"] == ["items.crud", "bitstreams.primary", bm.UNTAGGED]
        rows = matrix["rows"]
        assert rows["items.crud"]["dspace-9"]["state"] == "pass"
        assert rows["items.crud"]["cris-2025.02"]["state"] == "fail"
        assert rows["bitstreams.primary"]["dspace-9"]["state"] == "unsupported"
        assert rows[bm.UNTAGGED]["cris-2025.02"]["state"] == "n/a"
        assert rows["items.crud"]["dspace-10"]["state"] == "fail"
        assert rows["items.crud"]["dspace-10"]["setup_error"].startswith("REST API")

        md = (tmp_path / "matrix.md").read_text(encoding="utf-8")
        assert "| **dspace-9** DSPACE-9 | DSpace 9.1 |" in md
        assert "⚠ REST API not ready" in md
        assert "**cris-2025.02** · `items.crud` · items.crud_0: failed - assert 1 == 2" in md
        assert "`bitstreams.primary`<br><sub>" in md
        assert "## Regressions" not in md
        html_text = (tmp_path / "matrix.html").read_text(encoding="utf-8")
        assert "<title>DSpace toolkit compatibility matrix</title>" in html_text
        assert "title='items.crud_0: failed - assert 1 == 2'" in html_text
        assert json.loads((tmp_path / "matrix.json").read_text())["features"] == matrix["features"]

    def test_regressions_against_baseline(self, tmp_path):
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        write_reports(old_dir, report("dspace-9", [("items.crud", "passed", ""), ("items.upsert", "failed", "x")]))
        bm.write_matrix(str(old_dir))
        write_reports(new_dir, report("dspace-9", [("items.crud", "failed", "boom"), ("items.upsert", "failed", "x")]))
        matrix = bm.write_matrix(str(new_dir), baseline_path=str(old_dir / "matrix.json"))
        assert matrix["regressions"] == [{"feature": "items.crud", "target": "dspace-9"}]   # upsert was failing before
        assert "🔻" in (new_dir / "matrix.md").read_text(encoding="utf-8")
        assert "<h2>Regressions</h2>" in (new_dir / "matrix.html").read_text(encoding="utf-8")
        assert bm.main(["--reports", str(new_dir), "--baseline", str(old_dir / "matrix.json")]) == 1
        assert bm.main(["--reports", str(old_dir)]) == 0

    def test_feature_catalog(self):
        assert area("items.crud") == "items"
        assert all("." in f and FEATURES[f] for f in FEATURES)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class _TagLoader(yaml.SafeLoader):
    """safe_load that accepts Compose's !reset / !override tags"""


_TagLoader.add_multi_constructor("!", lambda loader, suffix, node: (
    loader.construct_mapping(node) if isinstance(node, yaml.MappingNode)
    else loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_scalar(node)))


def target(**kw):
    base = {"name": "dspace-9", "label": "DSpace 9", "family": "dspace", "base_version": "9", "index": 1,
            "compose_url": "https://example.org/docker-compose.yml", "port": 18090, "env": {},
            "admin_email": "admin@example.org", "admin_password": "pw", "startup_timeout": 60}
    return {**base, **kw}


class FakeRun:
    """Records docker/pytest commands and answers them."""

    def __init__(self, exec_rc=0, exec_out="", up_rc=0, pytest_rc=0, ps_state="running"):
        self.calls = []
        self.exec_rc, self.exec_out, self.up_rc, self.pytest_rc, self.ps_state = \
            exec_rc, exec_out, up_rc, pytest_rc, ps_state

    def __call__(self, command, env=None, cwd=None, capture_output=False, text=False):
        self.calls.append(command)
        if command[:2] == ["docker", "compose"]:
            verb = command[8]
            if verb == "up":
                return SimpleNamespace(returncode=self.up_rc, stdout="", stderr="pull access denied")
            if verb == "exec":
                return SimpleNamespace(returncode=self.exec_rc, stdout=self.exec_out, stderr="")
            if verb == "ps":
                return SimpleNamespace(returncode=0, stdout=json.dumps({"State": self.ps_state}) + "\n", stderr="")
            return SimpleNamespace(returncode=0, stdout=f"{verb} output", stderr="")
        return SimpleNamespace(returncode=self.pytest_rc, stdout="", stderr="")

    def verbs(self):
        return [c[8] if c[:2] == ["docker", "compose"] else "pytest" for c in self.calls]


class FakeHttp:
    def __init__(self, ready_after=0, compose_text="services: {}", fail_download=False):
        self.n = 0
        self.ready_after = ready_after
        self.compose_text = compose_text
        self.fail_download = fail_download

    def __call__(self, url, timeout=None, headers=None):
        if url.endswith(".yml"):
            if self.fail_download:
                raise requests.ConnectionError("offline")
            return Mock(text=self.compose_text, raise_for_status=Mock())
        self.n += 1
        if self.n <= self.ready_after:
            raise requests.ConnectionError("not yet")
        return Mock(status_code=200, json=Mock(return_value={"type": "root", "dspaceVersion": "DSpace 9.1"}))


def make_runner(tmp_path, run=None, http=None, keep=False, clock=None):
    ticks = iter(range(0, 100000, 5))
    return rm.MatrixRunner(workdir=str(tmp_path / "work"), reports_dir=str(tmp_path / "reports"), keep=keep,
                           run=run or FakeRun(), http_get=http or FakeHttp(), sleep=Mock(),
                           clock=clock or (lambda: next(ticks)), log=Mock())


class TestTargets:
    def test_repository_targets_file(self):
        targets = rm.load_targets()
        names = [t["name"] for t in targets]
        assert {"dspace-8", "dspace-9", "dspace-10", "cris-2024.02", "cris-2025.02"} <= set(names)
        assert all(t["admin_email"] and t["port"] for t in targets)

    @pytest.mark.parametrize("targets,error", [
        ([{"name": "a", "port": 1}], "missing"),
        ([{"name": "a", "port": 1, "compose_url": "u"}, {"name": "a", "port": 2, "compose_url": "u"}], "Duplicate target"),
        ([{"name": "a", "port": 1, "compose_url": "u"}, {"name": "b", "port": 1, "compose_url": "u"}], "Duplicate port"),
    ])
    def test_validation(self, tmp_path, targets, error):
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump({"targets": targets}))
        with pytest.raises(ValueError, match=error):
            rm.load_targets(str(path))

    def test_override_content(self):
        override = yaml.load(rm.compose_override(target(index=2, port=18100)), Loader=_TagLoader)
        dspace = override["services"]["dspace"]
        assert dspace["ports"] == ["127.0.0.1:18100:8080"]
        assert dspace["volumes"] == ["assetstore:/dspace/assetstore"]
        assert dspace["environment"]["dspace__P__server__P__url"] == "http://localhost:18100/server"
        assert dspace["depends_on"]["dspacedb"]["condition"] == "service_healthy"
        assert override["networks"]["dspacenet"]["ipam"]["config"][0]["subnet"] == "10.231.12.0/24"
        assert dspace["environment"]["proxies__P__trusted__P__ipranges"] == "10.231.12"
        assert rm.project_name(target(name="CRIS 2025.02")) == "dsit-cris-2025-02"
        assert rm.api_url(target()) == "http://localhost:18090/server/api"

    def test_local_cfg(self, tmp_path):
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump({
            "defaults": {"local_cfg": {"webui.user.assumelogin": True, "a.b": "x"}},
            "targets": [{"name": "one", "port": 1, "compose_url": "u", "local_cfg": {"a.b": "y", "list": ["1", "2"]}},
                        {"name": "two", "port": 2, "compose_url": "u"}]}))
        one, two = rm.load_targets(str(path))
        assert one["local_cfg"] == {"webui.user.assumelogin": True, "a.b": "y", "list": ["1", "2"]}
        assert two["local_cfg"] == {"webui.user.assumelogin": True, "a.b": "x"}
        text = rm.local_cfg_text(one)
        assert text.splitlines()[3:] == ["a.b = y", "list = 1", "list = 2", "webui.user.assumelogin = true"]
        assert rm.local_cfg_text(target(local_cfg={"x": False})).endswith("x = false\n")
        assert rm.local_cfg_text(target()).count("\n") == 3          # header only

    def test_local_cfg_is_written_and_mounted(self, tmp_path):
        runner = make_runner(tmp_path)
        upstream, override = runner.prepare(target(local_cfg={"webui.user.assumelogin": True}))
        local_cfg = tmp_path / "work" / "dspace-9" / "local.cfg"
        assert "webui.user.assumelogin = true" in local_cfg.read_text()
        volumes = yaml.load(open(override).read(), Loader=_TagLoader)["services"]["dspace"]["volumes"]
        assert volumes == ["assetstore:/dspace/assetstore",
                           {"type": "bind", "source": str(local_cfg), "target": "/dspace/config/local.cfg",
                            "read_only": True}]

    def test_repository_targets_allow_login_as(self):
        for t in rm.load_targets():
            assert t["local_cfg"].get("webui.user.assumelogin") is True, t["name"]

    @pytest.mark.skipif(not shutil.which("docker"), reason="docker CLI not installed")
    def test_override_merges_with_upstream_shape(self, tmp_path):
        upstream = tmp_path / "upstream.yml"
        upstream.write_text(yaml.safe_dump({
            "networks": {"dspacenet": {"ipam": {"config": [{"subnet": "172.23.0.0/16"}]}}},
            "services": {
                "dspace": {"image": "dspace/dspace:x", "container_name": "dspace", "build": {"context": "."},
                           "ports": ["8080:8080", "8000:8000"], "depends_on": ["dspacedb"],
                           "volumes": ["assetstore:/dspace/assetstore", "./dspace/config:/dspace/config"],
                           "networks": ["dspacenet"], "environment": {"a": "b"}},
                "dspacedb": {"image": "postgres:15", "container_name": "dspacedb", "ports": ["5432:5432"],
                             "networks": ["dspacenet"]},
                "dspacesolr": {"image": "dspace/dspace-solr:x", "container_name": "dspacesolr",
                               "ports": ["8983:8983"], "networks": ["dspacenet"]}},
            "volumes": {"assetstore": None}}))
        override = tmp_path / "override.yml"
        local_cfg = tmp_path / "local.cfg"
        local_cfg.write_text("webui.user.assumelogin = true\n")
        override.write_text(rm.compose_override(target(), str(local_cfg)))
        result = subprocess.run(["docker", "compose", "-p", "x", "-f", str(upstream), "-f", str(override),
                                 "config", "--format", "json"], capture_output=True, text=True)
        if result.returncode and "unknown" in result.stderr.lower():
            pytest.skip(f"docker compose too old for !reset/!override: {result.stderr[:200]}")
        assert result.returncode == 0, result.stderr
        merged = json.loads(result.stdout)["services"]
        assert "container_name" not in merged["dspace"] and "build" not in merged["dspace"]
        assert [p["published"] for p in merged["dspace"]["ports"]] == ["18090"]
        volumes = merged["dspace"]["volumes"]
        assert [v["source"] for v in volumes] == ["assetstore", str(local_cfg)]
        assert volumes[1]["target"] == "/dspace/config/local.cfg" and volumes[1]["read_only"] is True
        assert not merged["dspacedb"].get("ports")


class TestRunner:
    def test_successful_target(self, tmp_path):
        run = FakeRun()
        runner = make_runner(tmp_path, run=run, http=FakeHttp(ready_after=7))
        assert runner.run_target(target()) is True
        assert run.verbs() == ["up", "ps", "exec", "pytest", "logs", "down"]
        pytest_cmd = run.calls[3]
        assert "--it-target" in pytest_cmd and "addopts=" in pytest_cmd
        assert (tmp_path / "reports" / "dspace-9-docker.log").read_text() == "logs output"
        assert (tmp_path / "work" / "dspace-9" / "override.yml").exists()

    def test_failed_tests_and_keep(self, tmp_path):
        run = FakeRun(pytest_rc=1)
        assert make_runner(tmp_path, run=run, keep=True).run_target(target()) is False
        assert "down" not in run.verbs()

    def test_compose_up_failure_writes_setup_error(self, tmp_path):
        run = FakeRun(up_rc=1)
        assert make_runner(tmp_path, run=run).run_target(target()) is False
        data = json.loads((tmp_path / "reports" / "dspace-9.json").read_text())
        assert "pull access denied" in data["setup_error"]
        assert run.verbs()[-2:] == ["logs", "down"]

    def test_download_failure(self, tmp_path):
        runner = make_runner(tmp_path, http=FakeHttp(fail_download=True))
        assert runner.run_target(target()) is False
        assert "cannot download" in json.loads((tmp_path / "reports" / "dspace-9.json").read_text())["setup_error"]

    def test_backend_exit_detected_while_waiting(self, tmp_path):
        run = FakeRun(ps_state="exited")
        runner = make_runner(tmp_path, run=run, http=FakeHttp(ready_after=10_000))
        assert runner.run_target(target()) is False
        assert "exited" in json.loads((tmp_path / "reports" / "dspace-9.json").read_text())["setup_error"]

    def test_api_timeout(self, tmp_path):
        run = FakeRun(ps_state="")
        runner = make_runner(tmp_path, run=run, http=FakeHttp(ready_after=10_000))
        assert runner.run_target(target(startup_timeout=60)) is False
        assert "not ready after 60s" in json.loads((tmp_path / "reports" / "dspace-9.json").read_text())["setup_error"]

    def test_admin_already_exists_is_fine_other_errors_are_not(self, tmp_path):
        ok = FakeRun(exec_rc=1, exec_out="Error: an admin account with this email already exists")
        assert make_runner(tmp_path, run=ok).run_target(target()) is True
        bad = FakeRun(exec_rc=1, exec_out="Unknown option -c")
        assert make_runner(tmp_path / "b", run=bad).run_target(target()) is False

    def test_setup_commands_run_after_admin(self, tmp_path):
        run = FakeRun()
        runner = make_runner(tmp_path, run=run)
        cmd = "initialize-entities -f '/dspace/config/entities/relationship types.xml'"
        assert runner.run_target(target(setup=[cmd])) is True
        assert run.verbs() == ["up", "exec", "exec", "pytest", "logs", "down"]
        assert run.calls[2][-3:] == ["initialize-entities", "-f", "/dspace/config/entities/relationship types.xml"]
        assert any("done" in str(c) for c in runner.log.call_args_list)

    def test_failing_setup_is_logged_not_fatal(self, tmp_path):
        class SetupFails(FakeRun):
            def __call__(self, command, **kwargs):
                if "initialize-entities" in command:
                    self.calls.append(command)
                    return SimpleNamespace(returncode=2, stdout="", stderr="file not found")
                return super().__call__(command, **kwargs)

        runner = make_runner(tmp_path, run=SetupFails())
        assert runner.run_target(target(setup=["initialize-entities -f x.xml"])) is True
        assert any("failed (rc 2): file not found" in str(c) for c in runner.log.call_args_list)

    def test_repository_targets_load_entities_on_plain_dspace(self):
        for t in rm.load_targets():
            loads = any("initialize-entities" in c for c in t.get("setup") or [])
            assert loads == (t["family"] == "dspace"), t["name"]

    def test_cached_compose_file_is_reused(self, tmp_path):
        http = FakeHttp()
        runner = make_runner(tmp_path, http=http)
        downloads = Mock(wraps=http)
        runner.http_get = downloads
        runner.prepare(target())
        runner.prepare(target())
        assert sum(1 for c in downloads.call_args_list if c.args[0].endswith(".yml")) == 1
        runner.refresh = True
        runner.prepare(target())
        assert sum(1 for c in downloads.call_args_list if c.args[0].endswith(".yml")) == 2

    def test_ps_output_parsing(self, tmp_path):
        runner = make_runner(tmp_path)
        for stdout, state in [("", ""), ("not json\n", ""), ('[{"State": "Running"}]', "running"),
                              ("\n" + json.dumps({"State": "exited"}), "exited")]:
            runner.run = Mock(return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=""))
            assert runner.backend_state(target(), ("a", "b")) == state

    def test_existing_installation(self, tmp_path):
        run = FakeRun()
        assert make_runner(tmp_path, run=run).run_existing("prod-test", "https://x/server/api", "u", "p") is True
        assert run.verbs() == ["pytest"]


class TestCli:
    def factory(self, tmp_path, results):
        created = {}

        def make(**kwargs):
            runner = make_runner(tmp_path)
            runner.reports_dir = kwargs["reports_dir"]
            runner.run_target = Mock(side_effect=lambda t: results.get(t["name"], True))
            runner.run_existing = Mock(return_value=results.get("existing", True))
            created["runner"] = runner
            return runner
        return make, created

    def test_list(self, capsys):
        assert rm.main(["--list"]) == 0
        assert "dspace-10" in capsys.readouterr().out

    def test_unknown_target(self):
        with pytest.raises(SystemExit):
            rm.main(["-t", "dspace-99", "--no-matrix"])

    def test_existing_needs_credentials(self, monkeypatch):
        monkeypatch.delenv("DSPACE_IT_PASSWORD", raising=False)
        with pytest.raises(SystemExit):
            rm.main(["--existing", "--url", "http://x", "--user", "u", "--password", ""])

    def test_selected_targets_and_matrix(self, tmp_path):
        make, created = self.factory(tmp_path, {"dspace-9": True, "cris-2025.02": False})
        reports = tmp_path / "reports"
        code = rm.main(["-t", "dspace-9", "-t", "cris-2025.02", "--reports", str(reports)], runner_factory=make)
        assert code == 1
        assert [c.args[0]["name"] for c in created["runner"].run_target.call_args_list] == ["dspace-9", "cris-2025.02"]
        assert (reports / "matrix.md").exists()

    def test_existing_mode(self, tmp_path):
        make, created = self.factory(tmp_path, {"existing": True})
        code = rm.main(["--existing", "--url", "http://x", "--user", "u", "--password", "p", "--no-matrix",
                        "--reports", str(tmp_path / "r")], runner_factory=make)
        assert code == 0
        created["runner"].run_existing.assert_called_once_with("existing", "http://x", "u", "p")

    def test_regression_fails_the_run(self, tmp_path):
        reports = tmp_path / "reports"
        write_reports(reports, report("dspace-9", [("items.crud", "failed", "x")]))
        baseline = tmp_path / "baseline.json"
        old = tmp_path / "old"
        write_reports(old, report("dspace-9", [("items.crud", "passed", "")]))
        bm.write_matrix(str(old))
        (old / "matrix.json").replace(baseline)
        make, _ = self.factory(tmp_path, {"dspace-9": True})
        assert rm.main(["-t", "dspace-9", "--reports", str(reports), "--baseline", str(baseline)],
                       runner_factory=make) == 1
