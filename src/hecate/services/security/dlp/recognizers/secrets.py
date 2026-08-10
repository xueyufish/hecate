"""Secrets-based DLP recognizer wrapping the detect-secrets library.

detect-secrets is an optional dependency declared in the ``[security]``
extra (see tasks.md §16.3). The recognizer raises :class:`ImportError`
at construction time when the library is not importable, so callers
can decide whether to fail-fast or skip registration.

Maps detect-secrets plugin findings to canonical entity type names
defined in design.md §dlp-recognizers:
``AWS_ACCESS_KEY``, ``GITHUB_TOKEN``, ``JWT_TOKEN``, ``PRIVATE_KEY``.
"""

from __future__ import annotations

import logging

from hecate.services.security.dlp.recognizer import DLPRecognizer
from hecate.services.security.dlp.result import DLPFinding

logger = logging.getLogger(__name__)

try:
    import detect_secrets  # noqa: F401

    _HAS_DETECT_SECRETS = True
except ImportError:
    _HAS_DETECT_SECRETS = False


_PLUGIN_TO_ENTITY: dict[str, str] = {
    "AWSKeyDetector": "AWS_ACCESS_KEY",
    "JwtTokenDetector": "JWT_TOKEN",
    "PrivateKeyDetector": "PRIVATE_KEY",
    "GitHubTokenDetector": "GITHUB_TOKEN",
}


class SecretsRecognizer(DLPRecognizer):
    """Wrap a fixed set of detect-secrets plugins as DLP findings."""

    name = "secrets"
    supported_entities: list[str] = sorted(set(_PLUGIN_TO_ENTITY.values()))

    def __init__(self) -> None:
        if not _HAS_DETECT_SECRETS:
            raise ImportError(
                "SecretsRecognizer requires the 'detect-secrets' package. Install with: uv pip install -e '.[security]'"
            )

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
    ) -> list[DLPFinding]:
        from detect_secrets.core.plugins.core import (
            AWSKeyDetector,
            GitHubTokenDetector,
            JwtTokenDetector,
            PrivateKeyDetector,
        )
        from detect_secrets.core.plugins.initialized import initialize

        plugins = [
            (AWSKeyDetector, "AWS_ACCESS_KEY"),
            (GitHubTokenDetector, "GITHUB_TOKEN"),
            (JwtTokenDetector, "JWT_TOKEN"),
            (PrivateKeyDetector, "PRIVATE_KEY"),
        ]
        findings: list[DLPFinding] = []
        for plugin_cls, entity_type in plugins:
            if entities is not None and entity_type not in entities:
                continue
            try:
                plugin = initialize(plugin_cls)
                results = plugin.analyze_string(text, filename="<inline>")
            except Exception:
                logger.warning(
                    "detect-secrets plugin %s failed; skipping",
                    plugin_cls.__name__,
                    exc_info=True,
                )
                continue
            for result in results:
                secret_value = getattr(result, "secret_value", None) or ""
                if not secret_value:
                    continue
                start = text.find(secret_value)
                if start == -1:
                    continue
                findings.append(
                    DLPFinding(
                        entity_type=entity_type,
                        value=secret_value,
                        start=start,
                        end=start + len(secret_value),
                        score=1.0,
                        recognizer=self.name,
                    )
                )
        return findings
