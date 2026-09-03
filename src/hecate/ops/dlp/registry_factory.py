"""DLPRegistryFactory — builds a fully-populated DLPRecognizerRegistry.

The factory reads the org/workspace-scoped DLP configuration from the
DB and assembles a :class:`DLPRecognizerRegistry` containing:

* All four built-in recognizers (:class:`RegexRecognizer`,
  :class:`DictionaryRecognizer` with the canonical PII term list,
  :class:`SecretsRecognizer` when ``detect-secrets`` is installed,
  :class:`PresidioRecognizer` when ``presidio-analyzer`` is installed).
* One :class:`DictionaryRecognizer` per enabled
  :class:`DLPDictionaryModel` row (custom term lists).
* Custom regex patterns from :class:`DLPCustomRegexModel` are
  converted into a dynamic :class:`DLPRecognizer` subclass since
  :class:`RegexRecognizer`'s pattern set is class-level.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dlp import (
    DLPCustomRegexModel,
    DLPDictionaryModel,
)
from hecate.ops.dlp.recognizer import (
    DLPRecognizer,
    DLPRecognizerRegistry,
)
from hecate.ops.dlp.recognizers.dictionary import (
    DictionaryRecognizer,
)
from hecate.ops.dlp.recognizers.regex import RegexRecognizer
from hecate.ops.dlp.result import DLPFinding

logger = logging.getLogger(__name__)


class _CustomPatternRecognizer(DLPRecognizer):
    """Dynamic recognizer for a single user-defined regex pattern."""

    def __init__(
        self,
        *,
        name: str,
        entity_type: str,
        pattern: str,
    ) -> None:
        self.name = name
        self.supported_entities = [entity_type]
        self._pattern = re.compile(pattern)
        self._entity_type = entity_type

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
    ) -> list[DLPFinding]:
        if entities is not None and self._entity_type not in entities:
            return []
        return [
            DLPFinding(
                entity_type=self._entity_type,
                value=match.group(),
                start=match.start(),
                end=match.end(),
                score=1.0,
                recognizer=self.name,
            )
            for match in self._pattern.finditer(text)
        ]


class DLPRegistryFactory:
    """Materialize a :class:`DLPRecognizerRegistry` from the DLP DB tables."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        org_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None = None,
    ) -> DLPRecognizerRegistry:
        """Build a registry with built-ins + per-org/workspace custom rules."""
        registry = DLPRecognizerRegistry()
        for recognizer in self._builtin_recognizers():
            registry.register(recognizer)

        for custom in await self._list_custom_regex(org_id, workspace_id):
            if not custom.enabled or not custom.pattern:
                continue
            registry.register(
                _CustomPatternRecognizer(
                    name=custom.name,
                    entity_type=custom.entity_type,
                    pattern=custom.pattern,
                )
            )

        for dictionary in await self._list_dictionaries(org_id, workspace_id):
            if not dictionary.enabled or not dictionary.terms:
                continue
            registry.register(
                DictionaryRecognizer(
                    terms=list(dictionary.terms),
                    name=dictionary.name,
                    entity_type=dictionary.entity_type,
                    case_sensitive=dictionary.case_sensitive,
                )
            )

        return registry

    @staticmethod
    def _builtin_recognizers() -> list[DLPRecognizer]:
        """Return the canonical built-in recognizer set."""
        recognizers: list[DLPRecognizer] = [RegexRecognizer()]
        for name in ("detect_secrets", "presidio_analyzer"):
            if importlib.util.find_spec(name) is None:
                continue
            try:
                if name == "detect_secrets":
                    from hecate.ops.dlp.recognizers.secrets import (
                        SecretsRecognizer,
                    )

                    recognizers.append(SecretsRecognizer())
                else:
                    from hecate.ops.dlp.recognizers.presidio import (
                        PresidioRecognizer,
                    )

                    recognizers.append(PresidioRecognizer())
            except ImportError as exc:
                logger.info("Optional DLP recognizer %s not loadable: %s", name, exc)
        return recognizers

    async def _list_custom_regex(
        self,
        org_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
    ) -> list[DLPCustomRegexModel]:
        stmt = select(DLPCustomRegexModel).where(
            DLPCustomRegexModel.deleted == False  # noqa: E712
        )
        if org_id is not None:
            stmt = stmt.where(DLPCustomRegexModel.org_id == org_id)
        if workspace_id is not None:
            stmt = stmt.where(DLPCustomRegexModel.workspace_id == workspace_id)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def _list_dictionaries(
        self,
        org_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
    ) -> list[DLPDictionaryModel]:
        stmt = select(DLPDictionaryModel).where(
            DLPDictionaryModel.deleted == False  # noqa: E712
        )
        if org_id is not None:
            stmt = stmt.where(DLPDictionaryModel.org_id == org_id)
        if workspace_id is not None:
            stmt = stmt.where(DLPDictionaryModel.workspace_id == workspace_id)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())
