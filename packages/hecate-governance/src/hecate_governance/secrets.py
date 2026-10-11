"""Credential governance helpers (step7, task 4.3).

Plaintext-secret rejection for adapter-style configurations: a secret-shaped
field (``api_key``, ``secret``, ``token``, ``password`` …) whose value is not
a reference (``env:``/``file:``/``ref:``/``vault://`` prefix or a resolver
path) is rejected at load time — never silently accepted or downgraded.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

SECRET_KEY_PATTERN = re.compile(
    r"(?:^|[_-])(api[_-]?key|secret|token|password|passwd|credential|private[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)
REF_PREFIXES = ("env:", "file:", "ref:", "vault://")


def is_secret_reference(value: str) -> bool:
    """Whether the value looks like a resolvable secret reference."""
    return any(value.startswith(prefix) for prefix in REF_PREFIXES)


def find_plaintext_secrets(config: Mapping[str, object]) -> list[str]:
    """Return the paths of secret-shaped fields carrying plaintext values.

    Only top-level and one-level-nested mappings are scanned — adapter
    configs are flat. Non-string values under secret-shaped keys are flagged
    too (a boolean ``rotate_token: true`` is configuration, but an integer
    API key is a leaked credential).
    """
    findings: list[str] = []
    for key, value in config.items():
        path = str(key)
        if isinstance(value, Mapping):
            for nested_key, nested_value in value.items():
                _check(path + "." + str(nested_key), str(nested_key), nested_value, findings)
            continue
        _check(path, str(key), value, findings)
    return findings


def _check(path: str, key: str, value: object, findings: list[str]) -> None:
    if not SECRET_KEY_PATTERN.search(key):
        return
    if isinstance(value, bool):
        return
    if isinstance(value, str) and is_secret_reference(value):
        return
    findings.append(path)
