"""Tests for 9.1a injection-type detection on the LLM output side."""

from __future__ import annotations

import pytest

from hecate.ops.output_security.injection_detection.recognizers import (
    CodePythonRecognizer,
    SqlInjectionRecognizer,
    TemplateJinjaRecognizer,
    XssRecognizer,
)
from hecate.ops.output_security.injection_detection.recognizers.base import (
    make_recognizer_from_custom_pattern,
)
from hecate.ops.output_security.injection_detection.scanner import (
    resolve_config,
    scan,
    scan_with_guardrail_cfg,
)


class TestCodePythonRecognizer:
    def setup_method(self) -> None:
        self.recognizer = CodePythonRecognizer()

    def test_fires_on_eval(self) -> None:
        findings = self.recognizer.detect("eval(input('code: '))")
        assert findings
        assert findings[0].recognizer == "code_python"
        assert findings[0].entity_type == "CODE_PYTHON_INJECTION"

    def test_fires_on_exec(self) -> None:
        findings = self.recognizer.detect("exec(compile(src, '<>', 'exec'))")
        assert findings

    def test_no_finding_on_benign(self) -> None:
        assert self.recognizer.detect("This is a benign assistant response.") == []


class TestSqlInjectionRecognizer:
    def setup_method(self) -> None:
        self.recognizer = SqlInjectionRecognizer()

    def test_fires_on_drop_table(self) -> None:
        findings = self.recognizer.detect("DROP TABLE users;--")
        assert any(f.entity_type == "SQL_INJECTION" for f in findings)

    def test_fires_on_union_select(self) -> None:
        findings = self.recognizer.detect("1 UNION SELECT password FROM users")
        assert findings

    def test_no_finding_on_benign(self) -> None:
        assert self.recognizer.detect("Here is how a relational database works.") == []


class TestTemplateJinjaRecognizer:
    def setup_method(self) -> None:
        self.recognizer = TemplateJinjaRecognizer()

    def test_fires_on_ssti(self) -> None:
        findings = self.recognizer.detect("{{ config.__class__.__init__.__globals__['os'].popen('id').read() }}")
        assert findings

    def test_fires_on_import(self) -> None:
        findings = self.recognizer.detect("{% import os %}")
        assert findings

    def test_no_finding_on_benign(self) -> None:
        assert self.recognizer.detect("Use template variables like {{ name }} safely.") == []


class TestXssRecognizer:
    def setup_method(self) -> None:
        self.recognizer = XssRecognizer()

    def test_fires_on_img_onerror(self) -> None:
        findings = self.recognizer.detect("<img src=x onerror=alert(1)>")
        assert findings

    def test_fires_on_iframe(self) -> None:
        findings = self.recognizer.detect("<iframe src=javascript:alert(1)>")
        assert findings

    def test_no_finding_on_benign(self) -> None:
        assert self.recognizer.detect("The page rendered correctly.") == []


class TestCustomPatterns:
    def test_custom_pattern_fires(self) -> None:
        r = make_recognizer_from_custom_pattern(
            entity_type="MONGO_INJECTION",
            pattern=r"\$where\s*:\s*['\"]",
            severity="high",
            recognizer_id="custom_1",
        )
        findings = r.detect('$where: "this.password.match(/.*/)"')
        assert findings
        assert findings[0].entity_type == "MONGO_INJECTION"

    def test_custom_pattern_invalid_skipped(self) -> None:
        cfg = {"injection_detection": {"custom_patterns": [{"pattern": "[unclosed", "entity_type": "X"}]}}
        resolved = resolve_config(cfg)
        assert resolved.custom_recognizers == ()


class TestScanner:
    def test_default_action_is_audit(self) -> None:
        cfg = resolve_config(None)
        findings, action = scan("eval(input())", config=cfg)
        assert findings
        # audit < block, so overall is audit
        assert action.value == "audit"

    def test_per_type_action_override(self) -> None:
        cfg = resolve_config(
            {
                "injection_detection": {
                    "types": {"code_python": {"action": "block"}},
                }
            }
        )
        findings, action = scan("eval(input())", config=cfg)
        assert findings
        assert action.value == "block"

    def test_disabled_returns_no_findings(self) -> None:
        cfg = resolve_config({"injection_detection": {"enabled": False}})
        findings, action = scan("eval(input())", config=cfg)
        assert findings == []
        assert action.value == "allow"

    def test_most_restrictive_wins(self) -> None:
        cfg = resolve_config(
            {
                "injection_detection": {
                    "types": {
                        "code_python": {"action": "block"},
                        "xss": {"action": "sanitize"},
                    },
                }
            }
        )
        content = "eval(input()) <img src=x onerror=alert(1)>"
        findings, action = scan(content, config=cfg)
        assert findings
        assert action.value == "block"  # block wins over sanitize


@pytest.mark.asyncio
async def test_scan_with_guardrail_cfg_smoke() -> None:
    findings, _ = scan_with_guardrail_cfg("eval(input())", guardrail_cfg=None)
    assert findings
