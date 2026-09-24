"""Notebook editor preferences: a teammate's .env in the shared filespace stays out of every kernel."""

import tomllib

import pytest

from user_portal.notebook.start import write_preferences


def test_new_workspace_gets_preferences_without_dotenv(tmp_path):
    config = tmp_path / ".marimo.toml"
    write_preferences(config)
    preferences = tomllib.loads(config.read_text())
    assert preferences["display"]["theme"] == "dark"
    assert preferences["package_management"]["manager"] == "uv"
    assert preferences["runtime"]["dotenv"] == ["/dev/null"]
    before = config.read_text()
    write_preferences(config)
    assert config.read_text() == before


def test_existing_runtime_table_keeps_its_settings(tmp_path):
    config = tmp_path / ".marimo.toml"
    config.write_text('[runtime]  # saved by marimo\nauto_instantiate = false\n\n[runtime.watcher]\non = true\n')
    write_preferences(config)
    runtime = tomllib.loads(config.read_text())["runtime"]
    assert runtime == {"auto_instantiate": False, "dotenv": ["/dev/null"], "watcher": {"on": True}}


def test_runtime_subtable_only_still_gets_a_runtime_table(tmp_path):
    config = tmp_path / ".marimo.toml"
    config.write_text('[runtime.watcher]\non = true\n')
    write_preferences(config)
    assert tomllib.loads(config.read_text())["runtime"]["dotenv"] == ["/dev/null"]


def test_explicit_team_dotenv_choice_is_kept(tmp_path):
    config = tmp_path / ".marimo.toml"
    config.write_text('[package_management]\nmanager = "uv"\n\n[runtime]\ndotenv = ["team.env"]\n')
    before = config.read_text()
    write_preferences(config)
    assert config.read_text() == before


def test_marimo_resolves_no_dotenv_for_the_workspace(tmp_path, monkeypatch):
    pytest.importorskip("marimo")
    from marimo._config.manager import get_default_config_manager
    from marimo._config.utils import get_user_config_path

    (tmp_path / ".env").write_text("HTTPS_PROXY=http://attacker.invalid\n")
    write_preferences(tmp_path / ".marimo.toml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    get_user_config_path.cache_clear()
    try:
        config = get_default_config_manager(current_path=str(tmp_path)).get_config(hide_secrets=False)
    finally:
        get_user_config_path.cache_clear()
    assert config["runtime"]["dotenv"] == ["/dev/null"]
