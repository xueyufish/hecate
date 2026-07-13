"""Tests for plugin type ABCs, SDK, validation, CLI, and type-aware loader."""

from __future__ import annotations

from typing import Any

import pytest

from hecate.plugin.types import PLUGIN_TYPE_REGISTRY
from hecate.plugin.types.extension import ExtensionPluginABC
from hecate.plugin.types.model import ModelPluginABC
from hecate.plugin.types.tool import ToolPluginABC
from hecate.plugin.types.trigger import TriggerPluginABC
from hecate.plugin.validation import validate_api_surface

# ── 8.1 ToolPluginABC ──────────────────────────────────────────────────


class TestToolPluginABC:
    def test_abstract_methods_enforced(self):
        with pytest.raises(TypeError):
            ToolPluginABC()  # type: ignore[abstract]

    def test_valid_subclass(self):
        class MyTool(ToolPluginABC):
            @property
            def name(self) -> str:
                return "my-tool"

            @property
            def description(self) -> str:
                return "test"

            async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
                return {"ok": True}

        tool = MyTool()
        assert tool.name == "my-tool"


# ── 8.2 ExtensionPluginABC ────────────────────────────────────────────


class TestExtensionPluginABC:
    def test_partial_callbacks_skipped(self):
        class PartialExt(ExtensionPluginABC):
            def on_pre_tool(self, tool_name: str, args: dict[str, Any]) -> None:
                pass

        ext = PartialExt()
        assert ext.on_pre_tool("x", {}) is None
        assert ext.on_post_tool("x", {}) is None
        assert ext.on_pre_llm([], {}) is None
        assert ext.on_post_llm({}, {}) is None

    def test_all_callbacks_optional(self):
        ext = ExtensionPluginABC()
        assert ext.on_pre_llm([], {}) is None


# ── 8.4 TriggerPluginABC ──────────────────────────────────────────────


class TestTriggerPluginABC:
    def test_default_trigger_type(self):
        trigger = TriggerPluginABC()
        assert trigger.trigger_type == "webhook"

    def test_custom_trigger_type(self):
        class SchedTrigger(TriggerPluginABC):
            trigger_type = "schedule"

        assert SchedTrigger().trigger_type == "schedule"


# ── 8.5 ModelPluginABC ────────────────────────────────────────────────


class TestModelPluginABC:
    async def test_methods_raise_not_implemented(self):
        model = ModelPluginABC()
        with pytest.raises(NotImplementedError):
            await model.invoke([], {})

    def test_subclass_works(self):
        class MyModel(ModelPluginABC):
            async def invoke(self, messages, config):
                return {"content": "hi"}

            async def embed(self, text):
                return [0.1]

        m = MyModel()
        assert m is not None


# ── 8.6 Type-aware loader ────────────────────────────────────────────


class TestTypeAwareLoader:
    def test_registry_has_all_types(self):
        expected = {
            "tool",
            "extension",
            "trigger",
            "model",
            "channel",
            "evaluator",
            "auth_provider",
            "secret_provider",
        }
        assert set(PLUGIN_TYPE_REGISTRY.keys()) == expected

    def test_validate_correct_type(self):
        class GoodTool(ToolPluginABC):
            @property
            def name(self) -> str:
                return "good"

            @property
            def description(self) -> str:
                return "d"

            async def execute(self, params):
                return {}

        errors = validate_api_surface("tool", GoodTool())
        assert errors == []

    def test_validate_missing_method(self):
        class BadTool:
            name = "bad"
            description = "d"

        errors = validate_api_surface("tool", BadTool())
        assert len(errors) > 0
        assert "execute" in errors[0]

    def test_validate_unknown_type(self):
        errors = validate_api_surface("nonexistent", object())
        assert len(errors) == 1
        assert "Unknown" in errors[0]


# ── 8.7 validate_api_surface ─────────────────────────────────────────


class TestValidateApiSurface:
    def test_extension_no_required_methods(self):
        ext = ExtensionPluginABC()
        errors = validate_api_surface("extension", ext)
        assert errors == []

    def test_evaluator_requires_evaluate(self):
        class BadEval:
            name = "bad"

        errors = validate_api_surface("evaluator", BadEval())
        assert any("evaluate" in e for e in errors)


# ── 8.8 CLI template generator ───────────────────────────────────────


class TestPluginInitCLI:
    def test_scaffold_tool(self, tmp_path):
        from hecate.plugin.cli import init_plugin

        path = init_plugin("my-test-tool", "tool", str(tmp_path))
        import pathlib

        p = pathlib.Path(path)
        assert (p / "plugin.yaml").exists()
        assert (p / "__init__.py").exists()
        assert (p / "test_my_test_tool.py").exists()

    def test_scaffold_extension(self, tmp_path):
        from hecate.plugin.cli import init_plugin

        path = init_plugin("my-ext", "extension", str(tmp_path))
        import pathlib

        assert (pathlib.Path(path) / "plugin.yaml").exists()

    def test_invalid_type_rejected(self, tmp_path):
        from hecate.plugin.cli import init_plugin

        with pytest.raises(ValueError, match="Invalid type"):
            init_plugin("bad", "unknown", str(tmp_path))

    def test_existing_dir_rejected(self, tmp_path):
        from hecate.plugin.cli import init_plugin

        (tmp_path / "exists").mkdir()
        with pytest.raises(ValueError, match="already exists"):
            init_plugin("exists", "tool", str(tmp_path))


# ── 8.10 SDK module imports ──────────────────────────────────────────


class TestSDKImports:
    def test_import_tool_plugin(self):
        from hecate.plugin.sdk import ToolPluginABC as T

        assert T is ToolPluginABC

    def test_import_extension_plugin(self):
        from hecate.plugin.sdk import ExtensionPluginABC as E

        assert E is ExtensionPluginABC

    def test_import_all_types(self):
        from hecate.plugin.sdk import (
            AuthProviderABC,
            ChannelABC,
            EvaluatorABC,
            ExtensionPluginABC,
            ModelPluginABC,
            PluginContext,
            SecretProviderABC,
            ToolPluginABC,
            TriggerPluginABC,
        )

        assert all(
            [
                AuthProviderABC,
                ChannelABC,
                EvaluatorABC,
                ExtensionPluginABC,
                ModelPluginABC,
                PluginContext,
                SecretProviderABC,
                ToolPluginABC,
                TriggerPluginABC,
            ]
        )

    def test_plugin_context_config(self):
        from hecate.plugin.sdk import PluginContext

        ctx = PluginContext(config={"key": "val"}, permissions=("network:https",))
        assert ctx.config == {"key": "val"}
        assert ctx.check_permission("network:https") is True
        assert ctx.check_permission("filesystem:read") is False
