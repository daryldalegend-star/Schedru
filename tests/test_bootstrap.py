"""Tests for the first-run setup logic.

This code path used to live in start.bat, where it could not be run at all on
the machine this was developed on -- a Windows user hit a bug in it that no
test could have caught. It lives in Python now so it can be.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    """Load bootstrap.py with its paths pointed at a temp directory."""
    spec = importlib.util.spec_from_file_location("bootstrap_undertest", ROOT / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "VENV", tmp_path / ".venv")
    monkeypatch.setattr(module, "STAMP", tmp_path / ".venv" / ".deps-installed")
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(module, "ENV_EXAMPLE", tmp_path / ".env.example")
    return module


def test_missing_env_file_is_created_and_run_stops(bootstrap, tmp_path, capsys):
    (tmp_path / ".env.example").write_text("ANTHROPIC_API_KEY=\n")

    exit_code = bootstrap.ensure_env_file()

    assert exit_code == 1
    assert (tmp_path / ".env").exists()
    assert "paste your Anthropic API key" in capsys.readouterr().out


def test_empty_key_is_rejected(bootstrap, tmp_path, capsys):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=\n")

    assert bootstrap.ensure_env_file() == 1
    assert "no Anthropic API key" in capsys.readouterr().out


def test_whitespace_only_key_is_rejected(bootstrap, tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=   \n")
    assert bootstrap.ensure_env_file() == 1


def test_quoted_empty_key_is_rejected(bootstrap, tmp_path):
    (tmp_path / ".env").write_text('ANTHROPIC_API_KEY=""\n')
    assert bootstrap.ensure_env_file() == 1


def test_commented_out_key_is_rejected(bootstrap, tmp_path):
    (tmp_path / ".env").write_text("#ANTHROPIC_API_KEY=sk-real\n")
    assert bootstrap.ensure_env_file() == 1


def test_real_key_passes(bootstrap, tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-example\n")
    assert bootstrap.ensure_env_file() == 0


def test_key_among_other_lines_passes(bootstrap, tmp_path):
    (tmp_path / ".env").write_text(
        "# comment\n\nOTHER=1\nANTHROPIC_API_KEY=sk-ant-example\n"
    )
    assert bootstrap.ensure_env_file() == 0


def test_venv_creation_failure_reports_the_interpreter(bootstrap, monkeypatch, capsys):
    class Failed:
        returncode = 1

    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: Failed())

    assert bootstrap.ensure_venv() == 1
    assert "Could not create the virtual environment" in capsys.readouterr().out


def test_dependency_install_failure_blames_the_network_not_python(bootstrap, monkeypatch, capsys):
    class Result:
        returncode = 1

    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: Result())

    assert bootstrap.ensure_dependencies() == 1
    assert "Could not download the dependencies" in capsys.readouterr().out


def test_dependencies_are_not_reinstalled_once_stamped(bootstrap, tmp_path, monkeypatch):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / ".deps-installed").write_text("ok\n")

    calls = []
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: calls.append(a))

    assert bootstrap.ensure_dependencies() == 0
    assert calls == []


def test_python_39_from_xcode_is_rejected_with_a_clear_message(bootstrap, monkeypatch, capsys):
    # What macOS hands you if you accept the Xcode command line tools prompt.
    # pydantic would otherwise crash later on `time | None`.
    monkeypatch.setattr(bootstrap.sys, "version_info", (3, 9, 6))

    assert bootstrap.ensure_python_version() == 1
    out = capsys.readouterr().out
    assert "Python 3.9.6" in out
    assert "3.11" in out
    assert "Xcode" in out


def test_python_310_is_still_too_old(bootstrap, monkeypatch):
    monkeypatch.setattr(bootstrap.sys, "version_info", (3, 10, 14))
    assert bootstrap.ensure_python_version() == 1


def test_supported_python_passes(bootstrap, monkeypatch):
    for version in [(3, 11, 0), (3, 12, 7), (3, 14, 1)]:
        monkeypatch.setattr(bootstrap.sys, "version_info", version)
        assert bootstrap.ensure_python_version() == 0


def test_version_is_checked_before_building_the_venv(bootstrap, monkeypatch):
    """An old Python must be rejected before it creates a venv we can't use."""
    monkeypatch.setattr(bootstrap.sys, "version_info", (3, 9, 6))
    called = []
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: called.append(a))

    assert bootstrap.main([]) == 1
    assert called == []


def test_venv_python_path_is_platform_correct(bootstrap, monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap.os, "name", "nt")
    assert bootstrap.venv_python() == tmp_path / ".venv" / "Scripts" / "python.exe"

    monkeypatch.setattr(bootstrap.os, "name", "posix")
    assert bootstrap.venv_python() == tmp_path / ".venv" / "bin" / "python"
