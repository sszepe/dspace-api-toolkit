"""
.env handling of DSpaceConfig: found from the working directory, never
overrides variables that are already set, can be disabled or pointed at a file.
"""

import pytest

from dspace_api.config import DSpaceConfig


@pytest.fixture
def clean_env(monkeypatch):
    # setenv first so monkeypatch records the original state and removes
    # whatever load_dotenv() writes into os.environ after the test
    for name in ("DSPACE_URL", "DSPACE_TIMEOUT"):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    return monkeypatch


def test_reads_dotenv_from_working_directory(clean_env, tmp_path):
    (tmp_path / ".env").write_text("DSPACE_URL=http://from-dotenv/server/api\n")
    clean_env.chdir(tmp_path)
    assert DSpaceConfig().dspace_url == "http://from-dotenv/server/api"


def test_existing_environment_wins(clean_env, tmp_path):
    (tmp_path / ".env").write_text("DSPACE_TIMEOUT=99\n")
    clean_env.chdir(tmp_path)
    clean_env.setenv("DSPACE_TIMEOUT", "12")
    assert DSpaceConfig().timeout == 12


def test_disabled_and_explicit_path(clean_env, tmp_path):
    (tmp_path / ".env").write_text("DSPACE_TIMEOUT=99\n")
    other = tmp_path / "prod.env"
    other.write_text("DSPACE_TIMEOUT=7\n")
    clean_env.chdir(tmp_path)
    assert DSpaceConfig(env_file=False).timeout == 30
    assert DSpaceConfig(env_file=other).timeout == 7


def test_no_dotenv_anywhere(clean_env, tmp_path):
    clean_env.chdir(tmp_path)
    assert DSpaceConfig().dspace_url == "http://localhost:8080/server/api"
