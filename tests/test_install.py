"""Unit tests for ``install.py``.

Covers the pure helpers (env-file mutation), ``ensure_env_file`` (file I/O),
``docker_daemon_reachable`` (subprocess guard), and the "skip prompt when
any key is set" branch of ``prompt_for_llm_key``.

Subprocess and interactive paths beyond those (full ``main()`` flow) are
exercised end-to-end via the manual installer smoke test instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# install.py lives at the repo root; expose it for import without polluting
# pyproject's pythonpath globally.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from install import (  # noqa: E402  (sys.path tweak above)
    ENV_EXAMPLE,
    LLM_PROVIDERS,
    _docker_env,
    _parse_args,
    docker_daemon_reachable,
    ensure_env_file,
    prompt_for_llm_key,
    read_env_value,
    start_server,
    write_env_value,
)

# ---------------------------------------------------------------------------
# read_env_value
# ---------------------------------------------------------------------------


def test_read_env_value_basic():
    assert read_env_value("OPENAI_API_KEY=sk-abc\n", "OPENAI_API_KEY") == "sk-abc"


def test_read_env_value_strips_quotes():
    assert read_env_value('OPENAI_API_KEY="sk-abc"\n', "OPENAI_API_KEY") == "sk-abc"
    assert read_env_value("OPENAI_API_KEY='sk-abc'\n", "OPENAI_API_KEY") == "sk-abc"


def test_read_env_value_skips_comments_and_blanks():
    env_text = "# this is a comment\n\nANOTHER=value\nOPENAI_API_KEY=sk-real\n"
    assert read_env_value(env_text, "OPENAI_API_KEY") == "sk-real"
    assert read_env_value(env_text, "ANOTHER") == "value"


def test_read_env_value_missing_key_returns_none():
    assert read_env_value("OPENAI_API_KEY=sk-abc\n", "MISSING_KEY") is None
    assert read_env_value("# OPENAI_API_KEY=sk-abc\n", "OPENAI_API_KEY") is None


def test_read_env_value_empty_value_returns_none():
    assert read_env_value("OPENAI_API_KEY=\n", "OPENAI_API_KEY") is None
    assert read_env_value("OPENAI_API_KEY=   \n", "OPENAI_API_KEY") is None


# ---------------------------------------------------------------------------
# write_env_value
# ---------------------------------------------------------------------------


def test_write_env_value_replaces_existing():
    original = "OPENAI_API_KEY=old\nKALAN=preserved\n"
    new_text = write_env_value(original, "OPENAI_API_KEY", "new")
    assert "OPENAI_API_KEY=new\n" in new_text
    assert "KALAN=preserved\n" in new_text
    assert "old" not in new_text


def test_write_env_value_appends_when_missing():
    original = "KALAN=preserved\n"
    new_text = write_env_value(original, "OPENAI_API_KEY", "sk-abc")
    lines = new_text.splitlines()
    assert "KALAN=preserved" in lines
    assert "OPENAI_API_KEY=sk-abc" in lines


def test_write_env_value_normalizes_trailing_newline():
    original = "KALAN=preserved"
    new_text = write_env_value(original, "OPENAI_API_KEY", "sk-abc")
    assert new_text.endswith("\n")
    # exactly one trailing newline
    assert not new_text.endswith("\n\n")


# ---------------------------------------------------------------------------
# ensure_env_file
# ---------------------------------------------------------------------------


def test_ensure_env_file_creates_from_example(tmp_path, monkeypatch):
    monkeypatch.setattr("install.ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr("install.ENV_EXAMPLE", tmp_path / ".env.example")
    (tmp_path / ".env.example").write_text("KEY=value\n")

    ensure_env_file()

    assert (tmp_path / ".env").exists()
    assert (tmp_path / ".env").read_text() == "KEY=value\n"


def test_ensure_env_file_preserves_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    monkeypatch.setattr("install.ENV_FILE", env)
    monkeypatch.setattr("install.ENV_EXAMPLE", example)
    example.write_text("KEY=default\n")
    env.write_text("KEY=mine\n")

    ensure_env_file()

    assert env.read_text() == "KEY=mine\n"  # not overwritten


def test_ensure_env_file_missing_example_exits(tmp_path, monkeypatch):
    monkeypatch.setattr("install.ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr("install.ENV_EXAMPLE", tmp_path / ".env.example")
    # No .env.example

    with pytest.raises(SystemExit):
        ensure_env_file()


# ---------------------------------------------------------------------------
# docker_daemon_reachable
# ---------------------------------------------------------------------------


def test_docker_daemon_reachable_no_docker_binary(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert docker_daemon_reachable() is False


def test_docker_daemon_reachable_daemon_down():
    # docker binary exists but `docker info` fails (e.g. daemon not running)
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "docker info")),
    ):
        assert docker_daemon_reachable() is False


def test_docker_daemon_reachable_timeout():
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="docker info", timeout=5),
        ),
    ):
        assert docker_daemon_reachable() is False


def test_docker_daemon_reachable_ok():
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0)),
    ):
        assert docker_daemon_reachable() is True


# ---------------------------------------------------------------------------
# _docker_env — macOS Docker Desktop PATH fix
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Docker Desktop path")
def test_docker_env_macos_prepends_docker_app_bin():
    """On macOS with Docker Desktop installed, the helper dir is prepended to PATH.

    Without this, ``docker pull`` fails with::

        error getting credentials - err: exec: "docker-credential-desktop":
        executable file not found in $PATH

    when the installer is launched from a terminal that did not inherit the
    GUI shell's PATH (curl|bash, ssh session, IDE terminal, etc.).
    """
    docker_bin = Path("/Applications/Docker.app/Contents/Resources/bin")
    if not docker_bin.is_dir():
        pytest.skip("Docker Desktop not installed on this machine")
    env = _docker_env()
    assert env["PATH"].startswith(str(docker_bin) + os.pathsep)
    # The helper must be reachable via shutil.which with the augmented PATH.
    assert shutil.which("docker-credential-desktop", path=env["PATH"]) is not None


def test_docker_env_returns_dict_with_path():
    """The returned mapping always has PATH set so subprocess inherits it."""
    env = _docker_env()
    assert "PATH" in env
    assert isinstance(env["PATH"], str)
    assert env["PATH"]  # non-empty


def test_docker_env_preserves_other_vars():
    """Existing env vars (HOME, LANG, etc.) survive untouched."""
    env = _docker_env()
    # Whatever HOME/LANG/VIRTUAL_ENV happens to be in the test env, they
    # must still be present in the returned mapping.
    for key in ("HOME", "PATH"):
        assert key in env


# ---------------------------------------------------------------------------
# prompt_for_llm_key — skip branches
# ---------------------------------------------------------------------------


def _write_env(tmp_path: Path, content: str) -> None:
    (tmp_path / ".env").write_text(content)


def test_prompt_skips_when_any_provider_key_set(tmp_path, monkeypatch):
    monkeypatch.setattr("install.ENV_FILE", tmp_path / ".env")
    # ZAI_API_KEY is present; the prompt should be skipped entirely.
    _write_env(tmp_path, "ZAI_API_KEY=sk-zai\n")
    prompt_called = {"v": False}

    def _fail_prompt(*_args, **_kwargs):  # noqa: ANN001
        prompt_called["v"] = True
        raise AssertionError("Prompt.ask should not have been called")

    monkeypatch.setattr("rich.prompt.Prompt.ask", _fail_prompt)
    os.environ.pop("HECATE_NONINTERACTIVE", None)
    prompt_for_llm_key()
    assert prompt_called["v"] is False
    assert (tmp_path / ".env").read_text() == "ZAI_API_KEY=sk-zai\n"  # unchanged


def test_prompt_skips_in_noninteractive_with_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr("install.ENV_FILE", tmp_path / ".env")
    _write_env(tmp_path, "KEY=\n")  # no provider key set
    monkeypatch.setenv("HECATE_NONINTERACTIVE", "1")

    prompt_called = {"v": False}

    def _fail_prompt(*_args, **_kwargs):  # noqa: ANN001
        prompt_called["v"] = True
        raise AssertionError("Prompt.ask should not have been called")

    monkeypatch.setattr("rich.prompt.Prompt.ask", _fail_prompt)
    prompt_for_llm_key()
    assert prompt_called["v"] is False


def test_prompt_writes_chosen_provider_key(tmp_path, monkeypatch):
    monkeypatch.setattr("install.ENV_FILE", tmp_path / ".env")
    _write_env(tmp_path, "")
    monkeypatch.delenv("HECATE_NONINTERACTIVE", raising=False)

    # Pretend the user picked OpenAI (index 1 → first provider).
    answers = iter(["1", "sk-test-value"])

    def _stub_prompt(*_args, **_kwargs):  # noqa: ANN001
        return next(answers)

    monkeypatch.setattr("rich.prompt.Prompt.ask", _stub_prompt)
    prompt_for_llm_key()

    env_text = (tmp_path / ".env").read_text()
    assert "OPENAI_API_KEY=sk-test-value" in env_text


# ---------------------------------------------------------------------------
# provider list sanity sanity (catches typos in LLM_PROVIDERS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_var", [v for _, v, _ in LLM_PROVIDERS])
def test_every_provider_env_var_appears_in_env_example(env_var):
    """Regression guard: every LLM provider we advertise has a slot in .env.example."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert f"{env_var}=" in text, f"{env_var} missing from .env.example"


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


def test_parse_args_default_start_server_false(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["install.py"])
    args = _parse_args()
    assert args.start_server is False


def test_parse_args_start_server_flag_true(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["install.py", "--start-server"])
    args = _parse_args()
    assert args.start_server is True


# ---------------------------------------------------------------------------
# start_server
# ---------------------------------------------------------------------------


def test_start_server_skips_when_docker_unreachable():
    subprocess_calls: list[list[str]] = []

    def _spy_run(args, **_kwargs):  # noqa: ANN001
        subprocess_calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0)

    with patch("shutil.which", return_value=None), patch("subprocess.run", side_effect=_spy_run):
        start_server()

    assert subprocess_calls == [], "subprocess.run should NOT be called when docker daemon is unreachable"


def test_start_server_invokes_docker_compose_when_docker_up():
    captured: list[list[str]] = []

    def _capture_run(args, **_kwargs):  # noqa: ANN001
        captured.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0)

    with patch("install.docker_daemon_reachable", return_value=True), patch("subprocess.run", side_effect=_capture_run):
        start_server()

    assert len(captured) == 1
    cmd = captured[0]
    # The invocation should target docker compose and the hecate service.
    assert cmd[:3] == ["docker", "compose", "-f"]
    assert cmd[-1] == "hecate"
    assert "up" in cmd and "-d" in cmd
