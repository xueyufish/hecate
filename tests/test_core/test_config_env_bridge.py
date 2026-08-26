"""Tests for the ``.env`` → ``os.environ`` bridge in ``core.config``.

The bridge makes ``.env`` credentials (e.g. ``OPENAI_API_KEY``) visible to
third-party SDKs that read ``os.environ`` directly, mirroring what Docker
Compose does via ``env_file:``. Semantics under test:

- unset variables are exported
- real environment variables always win (no override)
- empty values are skipped (unset beats blank for credential probes)
- a missing ``.env`` file is a no-op
"""

from __future__ import annotations

from hecate.core.config import bridge_dotenv_to_environ


def test_exports_unset_variables(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("HECATE_TEST_BRIDGE_KEY=secret\n")
    environ: dict[str, str] = {}

    exported = bridge_dotenv_to_environ(str(env_file), environ)

    assert environ["HECATE_TEST_BRIDGE_KEY"] == "secret"
    assert exported == 1


def test_existing_environment_variables_win(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("HECATE_TEST_BRIDGE_KEY=from-file\n")
    environ = {"HECATE_TEST_BRIDGE_KEY": "from-env"}

    exported = bridge_dotenv_to_environ(str(env_file), environ)

    assert environ["HECATE_TEST_BRIDGE_KEY"] == "from-env"
    assert exported == 0


def test_empty_values_are_skipped(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("HECATE_TEST_EMPTY_KEY=\nHECATE_TEST_VALUELESS_KEY\n")
    environ: dict[str, str] = {}

    exported = bridge_dotenv_to_environ(str(env_file), environ)

    assert environ == {}
    assert exported == 0


def test_missing_env_file_is_noop(tmp_path):
    environ: dict[str, str] = {}

    exported = bridge_dotenv_to_environ(str(tmp_path / "does-not-exist.env"), environ)

    assert environ == {}
    assert exported == 0
