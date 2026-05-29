"""Tests for Provider config validation (_validate_config)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from hecate.api.management.model_providers import DEFAULT_CONFIG, _validate_config


class TestValidateConfig:
    def test_default_config_is_valid(self) -> None:
        """Test the DEFAULT_CONFIG passes validation."""
        _validate_config(DEFAULT_CONFIG)

    def test_valid_config_min_values(self) -> None:
        """Test minimum allowed values pass."""
        _validate_config({"timeout": 1, "max_retries": 0, "rate_limit_rpm": 1})

    def test_valid_config_max_values(self) -> None:
        """Test maximum allowed values pass."""
        _validate_config({"timeout": 300, "max_retries": 10, "rate_limit_rpm": 10000})

    def test_invalid_timeout_too_low(self) -> None:
        """Test timeout=0 raises 400."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_config({"timeout": 0})
        assert exc_info.value.status_code == 400
        assert "timeout" in exc_info.value.detail

    def test_invalid_timeout_too_high(self) -> None:
        """Test timeout=301 raises 400."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_config({"timeout": 301})
        assert exc_info.value.status_code == 400

    def test_invalid_max_retries_negative(self) -> None:
        """Test max_retries=-1 raises 400."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_config({"max_retries": -1})
        assert exc_info.value.status_code == 400
        assert "max_retries" in exc_info.value.detail

    def test_invalid_max_retries_too_high(self) -> None:
        """Test max_retries=11 raises 400."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_config({"max_retries": 11})
        assert exc_info.value.status_code == 400

    def test_invalid_rate_limit_zero(self) -> None:
        """Test rate_limit_rpm=0 raises 400."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_config({"rate_limit_rpm": 0})
        assert exc_info.value.status_code == 400
        assert "rate_limit_rpm" in exc_info.value.detail

    def test_invalid_rate_limit_too_high(self) -> None:
        """Test rate_limit_rpm=10001 raises 400."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_config({"rate_limit_rpm": 10001})
        assert exc_info.value.status_code == 400

    def test_partial_config_uses_defaults(self) -> None:
        """Test partial config uses default values for missing keys."""
        _validate_config({"timeout": 60})
        _validate_config({"max_retries": 5})
        _validate_config({"rate_limit_rpm": 120})

    def test_empty_config_uses_defaults(self) -> None:
        """Test empty dict uses all default values."""
        _validate_config({})
