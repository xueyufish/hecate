"""Tests for PluginManifest dataclass."""

from __future__ import annotations

from hecate.plugin.manifest import PluginManifest


class TestPluginManifest:
    """Tests for PluginManifest creation and properties."""

    def test_create_with_all_fields(self) -> None:
        """PluginManifest can be created with all fields specified."""
        manifest = PluginManifest(
            type="evaluator",
            name="faithfulness",
            version="1.0.0",
            api_version="1.0",
            min_platform_version="0.5.0",
            description="Detects ungrounded claims",
            permissions=["network:https"],
        )
        assert manifest.type == "evaluator"
        assert manifest.name == "faithfulness"
        assert manifest.version == "1.0.0"
        assert manifest.api_version == "1.0"
        assert manifest.min_platform_version == "0.5.0"
        assert manifest.description == "Detects ungrounded claims"
        assert manifest.permissions == ("network:https",)

    def test_create_with_defaults(self) -> None:
        """PluginManifest can be created with only required fields."""
        manifest = PluginManifest(
            type="evaluator",
            name="faithfulness",
            version="1.0.0",
        )
        assert manifest.type == "evaluator"
        assert manifest.name == "faithfulness"
        assert manifest.version == "1.0.0"
        assert manifest.api_version == ""
        assert manifest.min_platform_version == ""
        assert manifest.description == ""
        assert manifest.permissions == ()

    def test_immutable(self) -> None:
        """PluginManifest is frozen and cannot be modified."""
        manifest = PluginManifest(
            type="evaluator",
            name="faithfulness",
            version="1.0.0",
        )
        try:
            manifest.name = "changed"  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass

    def test_permissions_converted_to_tuple(self) -> None:
        """Permissions list is converted to tuple for immutability."""
        manifest = PluginManifest(
            type="evaluator",
            name="test",
            version="1.0.0",
            permissions=["network:https", "filesystem:read"],
        )
        assert isinstance(manifest.permissions, tuple)
        assert manifest.permissions == ("network:https", "filesystem:read")

    def test_equality_same_type_name_version(self) -> None:
        """Manifests with same type, name, version are equal."""
        m1 = PluginManifest(type="evaluator", name="test", version="1.0.0")
        m2 = PluginManifest(type="evaluator", name="test", version="1.0.0")
        assert m1 == m2

    def test_inequality_different_type(self) -> None:
        """Manifests with different type are not equal."""
        m1 = PluginManifest(type="evaluator", name="test", version="1.0.0")
        m2 = PluginManifest(type="channel", name="test", version="1.0.0")
        assert m1 != m2

    def test_inequality_different_name(self) -> None:
        """Manifests with different name are not equal."""
        m1 = PluginManifest(type="evaluator", name="test1", version="1.0.0")
        m2 = PluginManifest(type="evaluator", name="test2", version="1.0.0")
        assert m1 != m2

    def test_inequality_different_version(self) -> None:
        """Manifests with different version are not equal."""
        m1 = PluginManifest(type="evaluator", name="test", version="1.0.0")
        m2 = PluginManifest(type="evaluator", name="test", version="2.0.0")
        assert m1 != m2

    def test_hash_same_for_equal_manifests(self) -> None:
        """Equal manifests have the same hash."""
        m1 = PluginManifest(type="evaluator", name="test", version="1.0.0")
        m2 = PluginManifest(type="evaluator", name="test", version="1.0.0")
        assert hash(m1) == hash(m2)

    def test_hash_different_for_unequal_manifests(self) -> None:
        """Different manifests have different hashes (usually)."""
        m1 = PluginManifest(type="evaluator", name="test1", version="1.0.0")
        m2 = PluginManifest(type="evaluator", name="test2", version="1.0.0")
        # Hash collision is possible but unlikely for different names
        assert hash(m1) != hash(m2)
