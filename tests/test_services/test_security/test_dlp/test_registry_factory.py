"""Tests for DLPRegistryFactory."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dlp import (
    DLPCustomRegexModel,
    DLPDictionaryModel,
)
from hecate.ops.dlp.recognizers.dictionary import (
    DictionaryRecognizer,
)
from hecate.ops.dlp.registry_factory import (
    DLPRegistryFactory,
    _CustomPatternRecognizer,
)


class TestRegistryFactoryBuiltins:
    async def test_create_includes_regex_recognizer(self, db_session: AsyncSession) -> None:
        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=None)
        regex_recognizers = [r for r in registry.names() if r == "regex_pii"]
        assert len(regex_recognizers) == 1

    async def test_create_does_not_include_default_dictionary(self, db_session: AsyncSession) -> None:
        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=None)
        dict_names = [n for n in registry.names() if "dictionary" in n.lower()]
        assert dict_names == []

    async def test_create_skips_optional_recognizers_when_not_installed(self, db_session: AsyncSession) -> None:
        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=None)
        # secrets and presidio are optional; should not be present in this env
        assert "secrets" not in registry.names()
        assert "presidio" not in registry.names()


class TestRegistryFactoryCustomRegex:
    async def test_loads_enabled_custom_regex(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        rec = DLPCustomRegexModel(
            org_id=org_id,
            workspace_id=None,
            name="HECATE_TOKEN",
            pattern=r"HEC-[A-Z0-9]{16}",
            entity_type="HECATE_TOKEN",
            enabled=True,
        )
        db_session.add(rec)
        await db_session.flush()

        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=org_id)

        assert "HECATE_TOKEN" in registry.names()
        custom = registry.get("HECATE_TOKEN")
        assert isinstance(custom, _CustomPatternRecognizer)

    async def test_skips_disabled_custom_regex(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        rec = DLPCustomRegexModel(
            org_id=org_id,
            workspace_id=None,
            name="DISABLED",
            pattern=r"X+",
            entity_type="X",
            enabled=False,
        )
        db_session.add(rec)
        await db_session.flush()

        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=org_id)

        assert "DISABLED" not in registry.names()

    async def test_filters_by_org_id(self, db_session: AsyncSession) -> None:
        org_a = uuid.uuid4()
        org_b = uuid.uuid4()
        for org_id, name in [(org_a, "REGEX_A"), (org_b, "REGEX_B")]:
            db_session.add(
                DLPCustomRegexModel(
                    org_id=org_id,
                    workspace_id=None,
                    name=name,
                    pattern=r"X+",
                    entity_type="X",
                    enabled=True,
                )
            )
        await db_session.flush()

        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=org_a)

        assert "REGEX_A" in registry.names()
        assert "REGEX_B" not in registry.names()

    async def test_skips_empty_pattern(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        db_session.add(
            DLPCustomRegexModel(
                org_id=org_id,
                workspace_id=None,
                name="EMPTY",
                pattern="",
                entity_type="X",
                enabled=True,
            )
        )
        await db_session.flush()

        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=org_id)

        assert "EMPTY" not in registry.names()


class TestRegistryFactoryDictionaries:
    async def test_loads_enabled_dictionary(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        db_session.add(
            DLPDictionaryModel(
                org_id=org_id,
                workspace_id=None,
                name="codenames",
                entity_type="CODENAME",
                terms=["Apollo", "Artemis"],
                case_sensitive=True,
                enabled=True,
            )
        )
        await db_session.flush()

        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=org_id)

        assert "codenames" in registry.names()
        dict_rec = registry.get("codenames")
        assert isinstance(dict_rec, DictionaryRecognizer)

    async def test_skips_disabled_dictionary(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        db_session.add(
            DLPDictionaryModel(
                org_id=org_id,
                workspace_id=None,
                name="disabled",
                entity_type="X",
                terms=["a"],
                case_sensitive=False,
                enabled=False,
            )
        )
        await db_session.flush()

        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=org_id)

        assert "disabled" not in registry.names()

    async def test_skips_empty_terms(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        db_session.add(
            DLPDictionaryModel(
                org_id=org_id,
                workspace_id=None,
                name="empty",
                entity_type="X",
                terms=[],
                case_sensitive=False,
                enabled=True,
            )
        )
        await db_session.flush()

        factory = DLPRegistryFactory(db_session)
        registry = await factory.create(org_id=org_id)

        assert "empty" not in registry.names()


class TestCustomPatternRecognizer:
    def test_creates_findings(self) -> None:
        rec = _CustomPatternRecognizer(
            name="HECATE_TOKEN",
            entity_type="HECATE_TOKEN",
            pattern=r"HEC-[A-Z0-9]{16}",
        )
        findings = rec.analyze("see HEC-ABC1234567890XYZ and other text")
        assert len(findings) == 1
        assert findings[0].value == "HEC-ABC1234567890XYZ"
        assert findings[0].entity_type == "HECATE_TOKEN"
        assert findings[0].score == 1.0

    def test_filters_by_entity_type(self) -> None:
        rec = _CustomPatternRecognizer(
            name="HECATE_TOKEN",
            entity_type="HECATE_TOKEN",
            pattern=r"HEC-[A-Z0-9]{16}",
        )
        assert rec.analyze("HEC-ABC1234567890XYZ", entities=["OTHER"]) == []

    def test_empty_text(self) -> None:
        rec = _CustomPatternRecognizer(
            name="X",
            entity_type="X",
            pattern=r"X+",
        )
        assert rec.analyze("") == []
