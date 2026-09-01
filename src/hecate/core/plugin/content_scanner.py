"""Plugin content scanner — deterministic rule engine (feature 5.13a).

Implements the ``ScanStage`` protocol that 5.5c reserved: a pure rule
engine (regex + heuristics + codepoint analysis) over package text
content with deterministic, auditable findings. Design decisions live in
``openspec/changes/plugin-content-scanning/design.md``:

- five rule categories (injection, invisible-Unicode, secrets,
  allowed-tools audit, suspicious URLs) with severity assigned by a
  file-role x rule matrix (runtime exposure based);
- obfuscation layer v1: NFKC normalization plus bounded base64/hex
  decode-and-rescan, findings record the exposing ``transform``;
- fail-closed budgets: oversize text files produce findings instead of
  being silently skipped, and exceeding a time budget raises
  :class:`ScannerError` (callers reject the install);
- evidence is redacted to an 8-character fingerprint — secret material
  and complete payloads are never persisted.
"""

from __future__ import annotations

import base64
import binascii
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hecate.core.plugin.agent_plugins import ScanResult, ScanStage

SCANNER_VERSION = "rule-engine-1"

SEVERITY_ORDER: dict[str, int] = {"low": 1, "medium": 2, "high": 3}

DEFAULT_FILE_CAP_BYTES = 1024 * 1024
FILE_TIME_BUDGET_SECONDS = 10.0
PACKAGE_TIME_BUDGET_SECONDS = 120.0
MAX_DECODED_BLOB_BYTES = 64 * 1024
ENCODED_BLOB_MIN_CHARS = 40
PRINTABLE_RATIO_MIN = 0.9

TAG_RUN_LIMIT = 10
SUSPICIOUS_TOTAL_LIMIT = 100

EVIDENCE_MAX_CHARS = 8


class ScannerError(RuntimeError):
    """Raised when the scanner itself fails — callers must fail closed."""


# --- Rule definitions -------------------------------------------------------


@dataclass(frozen=True)
class RuleSpec:
    """A single deterministic detection rule."""

    rule_id: str
    category: str
    pattern: re.Pattern[str]
    intrinsic: str
    description: str


def _rule(rule_id: str, category: str, source: str, intrinsic: str, description: str) -> RuleSpec:
    return RuleSpec(
        rule_id=rule_id,
        category=category,
        pattern=re.compile(source),
        intrinsic=intrinsic,
        description=description,
    )


RULES: tuple[RuleSpec, ...] = (
    _rule(
        "INJ-override",
        "injection",
        r"(?i)\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|rules?|prompts?|context)\b",
        "high",
        "instruction-override phrasing",
    ),
    _rule(
        "INJ-disregard",
        "injection",
        r"(?i)\bdisregard\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|rules?|prompts?)\b",
        "high",
        "instruction-override phrasing",
    ),
    _rule(
        "INJ-fake-tag",
        "injection",
        r"(?i)<\|?(?:im_start|im_end|endoftext|system)\|?>",
        "high",
        "fake LLM control tag",
    ),
    _rule(
        "INJ-reveal-prompt",
        "injection",
        r"(?i)\b(?:reveal|recite|repeat|dump)\s+(?:the\s+|your\s+)?(?:full\s+|complete\s+|exact\s+|verbatim\s+)?system\s+prompt\b",
        "medium",
        "system prompt exfiltration request",
    ),
    _rule(
        "INJ-curl-shell",
        "injection",
        r"(?i)\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b",
        "high",
        "download piped into a shell",
    ),
    _rule(
        "INJ-reverse-shell",
        "injection",
        r"(?i)(?:\bnc(?:at)?\s+(?:-\w+\s+){0,4}-e\b|/dev/tcp/)",
        "high",
        "reverse shell payload",
    ),
    _rule(
        "INJ-keydump",
        "injection",
        r"\$\{?(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|AWS_SECRET_ACCESS_KEY|GITHUB_TOKEN)\}?",
        "medium",
        "credential environment variable reference",
    ),
    _rule(
        "SEC-private-key",
        "secret",
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----",
        "high",
        "private key material",
    ),
    _rule("SEC-aws-key", "secret", r"\bAKIA[0-9A-Z]{16}\b", "high", "AWS access key"),
    _rule("SEC-github-token", "secret", r"\bgh[pousr]_[A-Za-z0-9]{16,}\b", "high", "GitHub token"),
    _rule("SEC-openai-key", "secret", r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b", "high", "OpenAI/Anthropic API key"),
    _rule(
        "SEC-jwt",
        "secret",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b",
        "medium",
        "JWT token",
    ),
    _rule(
        "SEC-conn-string",
        "secret",
        r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)"
        r"://[^\s\"'<>]{3,}:[^\s\"'<>]{3,}@",
        "medium",
        "connection string with credentials",
    ),
    _rule(
        "URL-paste-site",
        "url",
        r"(?i)\bhttps?://(?:[a-z0-9-]+\.)*(?:pastebin\.com|paste\.ee|rentry\.co|justpaste\.it|controlc\.com|dogbin\.com|dpaste\.org|0x0\.st|transfer\.sh|termbin\.com)\b",
        "medium",
        "paste-site URL (exfiltration channel)",
    ),
    _rule("URL-ip-literal", "url", r"\bhttps?://\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?", "medium", "IP-literal endpoint"),
    _rule("URL-punycode", "url", r"(?i)\bhttps?://[a-z0-9.-]*xn--", "medium", "punycode (homograph) domain"),
)

# Only high-confidence rules re-run on decoded blobs.
HIGH_TIER_RULES: tuple[RuleSpec, ...] = tuple(r for r in RULES if r.intrinsic == "high")

# --- File roles and the severity matrix --------------------------------------
#
# Roles reflect how content reaches agent context (SkillLoader injects
# description + instructions into the <skill> block; mcp.json values are
# credential surfaces; nested files are read on demand; README/catalog
# text is mostly human-facing). Caps follow design D4.

ROLE_SEVERITY_CAP: dict[str, dict[str, str]] = {
    "skill-frontmatter": {
        "injection": "high",
        "secret": "high",
        "unicode": "high",
        "tools-audit": "high",
        "url": "medium",
        "oversize": "high",
    },
    "skill-body": {
        "injection": "high",
        "secret": "medium",
        "unicode": "high",
        "url": "medium",
        "oversize": "high",
    },
    "mcp-credentials": {
        "injection": "medium",
        "secret": "high",
        "unicode": "medium",
        "url": "medium",
        "oversize": "high",
    },
    "nested": {
        "injection": "medium",
        "secret": "medium",
        "unicode": "medium",
        "url": "low",
        "oversize": "high",
    },
    "readme": {
        "injection": "low",
        "secret": "medium",
        "unicode": "low",
        "url": "low",
        "oversize": "medium",
    },
    "catalog": {
        "injection": "low",
        "secret": "low",
        "unicode": "medium",
        "url": "low",
        "oversize": "high",
    },
}

_DEFAULT_CAP = "medium"


def _cap(role: str, category: str) -> str:
    return ROLE_SEVERITY_CAP.get(role, {}).get(category, _DEFAULT_CAP)


def _capped(intrinsic: str, role: str, category: str) -> str:
    cap = _cap(role, category)
    if SEVERITY_ORDER[intrinsic] <= SEVERITY_ORDER[cap]:
        return intrinsic
    return cap


def classify_file(rel: Path) -> str:
    """Classify a package-relative path into a scan role.

    Returns ``"skill"`` for SKILL.md files (frontmatter and body are
    scanned under separate roles), or a concrete role for the rest.
    """
    parts = rel.parts
    name = rel.name
    if name == "SKILL.md" and len(parts) >= 2:
        return "skill"
    if name == "mcp.json" and len(parts) == 1:
        return "mcp-credentials"
    if len(parts) == 1 and name.lower().startswith("readme"):
        return "readme"
    if name == "plugin.json" and len(parts) == 1:
        return "catalog"
    return "nested"


def split_skill_md(text: str) -> tuple[str | None, str]:
    """Split SKILL.md into (frontmatter, body); frontmatter is None when absent."""
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return None, text
    lines = stripped.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return None, text


def compute_verdict(findings: list[dict[str, Any]], block_at: str = "high") -> str:
    """Map the highest finding severity onto allow / warn / block."""
    threshold = SEVERITY_ORDER.get(block_at, SEVERITY_ORDER["high"])
    top = max((SEVERITY_ORDER.get(f.get("severity", "low"), 1) for f in findings), default=0)
    if top >= threshold:
        return "block"
    if top >= SEVERITY_ORDER["medium"]:
        return "warn"
    return "allow"


# --- Invisible-Unicode analysis ----------------------------------------------


def _is_tag_codepoint(ch: str) -> bool:
    cp = ord(ch)
    return 0xE0000 <= cp <= 0xE007F


def _is_suspicious_codepoint(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x200B <= cp <= 0x200D
        or cp in (0x2060, 0xFEFF)
        or 0x202A <= cp <= 0x202E
        or 0x2066 <= cp <= 0x2069
        or 0xFE00 <= cp <= 0xFE0F
        or _is_tag_codepoint(ch)
    )


_SUSPICIOUS_RE = re.compile("[\u200b-\u200d\u202a-\u202e\u2060\u2066-\u2069\ufeff\ufe00-\ufe0f\U000e0000-\U000e007f]")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _redact(text: str) -> str:
    """Evidence fingerprint: first 8 characters plus length, never the full match."""
    if len(text) <= EVIDENCE_MAX_CHARS:
        return text
    return f"{text[:EVIDENCE_MAX_CHARS]}…({len(text)} chars)"


def _finding(
    rule_id: str,
    category: str,
    severity: str,
    file: str,
    line: int | None,
    transform: str,
    evidence: str,
    description: str = "",
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": severity,
        "file": file,
        "line": line,
        "transform": transform,
        "evidence": evidence,
        "description": description,
    }


_BASE64_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_HEX_CANDIDATE_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40,}(?![0-9a-fA-F])")


def _mostly_printable(raw: bytes) -> bool:
    if not raw:
        return False
    printable = sum(1 for b in raw if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(raw) >= PRINTABLE_RATIO_MIN


def _decoded_blobs(text: str) -> list[tuple[bytes, str]]:
    """Candidate encoded blobs that decode to mostly-printable text."""
    out: list[tuple[bytes, str]] = []
    for m in _BASE64_CANDIDATE_RE.finditer(text):
        blob = m.group(0)
        try:
            raw = base64.b64decode(blob, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(raw) <= MAX_DECODED_BLOB_BYTES and _mostly_printable(raw):
            out.append((raw, "base64"))
    for m in _HEX_CANDIDATE_RE.finditer(text):
        blob = m.group(0)
        if len(blob) // 2 > MAX_DECODED_BLOB_BYTES:
            continue
        try:
            raw = binascii.unhexlify(blob)
        except (binascii.Error, ValueError):
            continue
        if _mostly_printable(raw):
            out.append((raw, "hex"))
    return out


_SHELL_TOOL_MARKERS = ("bash", "zsh", "fish", "powershell", "shell", "terminal", "process", "exec", "cmd")
_WRITE_TOOL_MARKERS = ("write", "edit", "filesystem", "delete", "remove")
_NET_TOOL_MARKERS = ("fetch", "web", "http", "network", "curl", "request", "download")


class ContentScanner:
    """Deterministic content scanner over a materialized package tree."""

    def __init__(self, block_at: str = "high", file_cap_bytes: int = DEFAULT_FILE_CAP_BYTES) -> None:
        self._block_at = block_at if block_at in SEVERITY_ORDER else "high"
        self._file_cap = file_cap_bytes
        self._file_deadline = 0.0

    def scan(self, package_root: Path) -> ScanResult:
        """Scan every scannable file under *package_root* (excluding .git)."""
        deadline = time.perf_counter() + PACKAGE_TIME_BUDGET_SECONDS
        findings: list[dict[str, Any]] = []
        # One finding per rule per file; frontmatter (highest caps) is
        # scanned before the body so the surviving severity is the max.
        dedup: set[tuple[str, str]] = set()
        files = sorted(p for p in package_root.rglob("*") if ".git" not in p.parts and p.is_file())
        for path in files:
            if time.perf_counter() > deadline:
                raise ScannerError(f"package scan time budget exceeded at {path.name}")
            rel = path.relative_to(package_root).as_posix()
            self._scan_file(path, rel, findings, dedup)
        return ScanResult(
            verdict=compute_verdict(findings, self._block_at),
            findings=findings,
            scanner_version=SCANNER_VERSION,
        )

    # -- per-file ---------------------------------------------------------

    def _scan_file(self, path: Path, rel: str, findings: list[dict[str, Any]], dedup: set[tuple[str, str]]) -> None:
        with path.open("rb") as fh:
            head = fh.read(8192)
        if b"\x00" in head:
            return  # binary asset: skip without a finding
        role_class = classify_file(Path(rel))
        primary = "skill-body" if role_class == "skill" else role_class

        size = path.stat().st_size
        if size > self._file_cap:
            findings.append(
                _finding(
                    "OVERSIZED-TEXT",
                    "oversize",
                    _cap(primary, "oversize"),
                    rel,
                    None,
                    "none",
                    f"{size} bytes exceeds scan cap {self._file_cap} bytes (content not scanned)",
                    "oversized text file",
                )
            )
            return

        self._file_deadline = time.perf_counter() + FILE_TIME_BUDGET_SECONDS
        text = path.read_text(encoding="utf-8", errors="replace")

        self._scan_unicode(text, primary, rel, findings)

        if role_class == "skill":
            front, body = split_skill_md(text)
            if front is not None:
                self._apply_rules(front, "skill-frontmatter", rel, "none", findings, dedup)
                self._audit_allowed_tools(front, rel, findings)
            self._apply_rules(body, "skill-body", rel, "none", findings, dedup)
        else:
            self._apply_rules(text, role_class, rel, "none", findings, dedup)

        normalized = unicodedata.normalize("NFKC", text)
        if normalized != text:
            if role_class == "skill":
                front, body = split_skill_md(normalized)
                if front is not None:
                    self._apply_rules(front, "skill-frontmatter", rel, "nfkc", findings, dedup)
                self._apply_rules(body, "skill-body", rel, "nfkc", findings, dedup)
            else:
                self._apply_rules(normalized, role_class, rel, "nfkc", findings, dedup)

        for raw, transform in _decoded_blobs(text):
            decoded = raw.decode("utf-8", errors="ignore")
            if role_class == "skill":
                _front, body = split_skill_md(decoded)
                self._apply_rules(body, "skill-body", rel, transform, findings, dedup, only=HIGH_TIER_RULES)
            else:
                self._apply_rules(decoded, role_class, rel, transform, findings, dedup, only=HIGH_TIER_RULES)

    def _apply_rules(
        self,
        text: str,
        role: str,
        rel: str,
        transform: str,
        findings: list[dict[str, Any]],
        dedup: set[tuple[str, str]],
        only: tuple[RuleSpec, ...] | None = None,
    ) -> None:
        for rule in only or RULES:
            if (rule.rule_id, rel) in dedup:
                continue
            if time.perf_counter() > self._file_deadline > 0:
                raise ScannerError(f"file scan time budget exceeded at {rel}")
            m = rule.pattern.search(text)
            if m is None:
                continue
            severity = _capped(rule.intrinsic, role, rule.category)
            line = text.count("\n", 0, m.start()) + 1 if transform in ("none", "nfkc") else None
            findings.append(
                _finding(
                    rule.rule_id,
                    rule.category,
                    severity,
                    rel,
                    line,
                    transform,
                    _redact(m.group(0)),
                    rule.description,
                )
            )
            dedup.add((rule.rule_id, rel))

    def _scan_unicode(self, text: str, role: str, rel: str, findings: list[dict[str, Any]]) -> None:
        if _SUSPICIOUS_RE.search(text) is None and _ANSI_ESCAPE_RE.search(text) is None:
            return
        max_tag_run = 0
        run = 0
        total = 0
        for ch in text:
            if _is_tag_codepoint(ch):
                run += 1
                if run > max_tag_run:
                    max_tag_run = run
                total += 1
            else:
                run = 0
                if _is_suspicious_codepoint(ch):
                    total += 1
        total += len(_ANSI_ESCAPE_RE.findall(text))
        if max_tag_run > TAG_RUN_LIMIT:
            findings.append(
                _finding(
                    "UNI-tag-run",
                    "unicode",
                    _capped("high", role, "unicode"),
                    rel,
                    None,
                    "none",
                    f"{max_tag_run} contiguous tag codepoints (hidden ASCII smuggling)",
                    "invisible-Unicode tag run",
                )
            )
        if total > SUSPICIOUS_TOTAL_LIMIT:
            findings.append(
                _finding(
                    "UNI-total",
                    "unicode",
                    _capped("medium", role, "unicode"),
                    rel,
                    None,
                    "none",
                    f"{total} suspicious codepoints in file",
                    "suspicious codepoint density",
                )
            )

    def _audit_allowed_tools(self, frontmatter: str, rel: str, findings: list[dict[str, Any]]) -> None:
        try:
            front = yaml.safe_load(frontmatter)
        except yaml.YAMLError:
            return
        if not isinstance(front, dict):
            return
        allowed = front.get("allowed-tools") if "allowed-tools" in front else front.get("allowed_tools")
        if allowed is None:
            return
        entries = [str(a) for a in allowed] if isinstance(allowed, list) else [str(allowed)]
        cap = _cap("skill-frontmatter", "tools-audit")

        def _audit(rule_id: str, intrinsic: str, matched: list[str], description: str) -> None:
            if not matched:
                return
            severity = intrinsic if SEVERITY_ORDER[intrinsic] <= SEVERITY_ORDER[cap] else cap
            findings.append(
                _finding(
                    rule_id,
                    "tools-audit",
                    severity,
                    rel,
                    None,
                    "none",
                    ",".join(matched)[:64],
                    description,
                )
            )

        if "*" in entries:
            _audit("TOOLS-ALL", "high", ["*"], "wildcard tool pre-authorization")
        _audit(
            "TOOLS-SHELL",
            "medium",
            [e for e in entries if any(k in e.lower() for k in _SHELL_TOOL_MARKERS)],
            "shell execution pre-authorization",
        )
        _audit(
            "TOOLS-WRITE",
            "low",
            [e for e in entries if any(k in e.lower() for k in _WRITE_TOOL_MARKERS)],
            "filesystem write pre-authorization",
        )
        _audit(
            "TOOLS-NET",
            "low",
            [e for e in entries if any(k in e.lower() for k in _NET_TOOL_MARKERS)],
            "network access pre-authorization",
        )


class RuleEngineScanStage(ScanStage):
    """Production scan stage (5.13a) replacing the 5.5c no-op.

    Reads its knobs from settings lazily so unit tests can drive
    :class:`ContentScanner` without configuration.
    """

    def __init__(self, block_at: str | None = None, file_cap_bytes: int | None = None) -> None:
        self._block_at = block_at
        self._file_cap_bytes = file_cap_bytes

    def scan(self, package_root: Path) -> ScanResult:
        from hecate.core.config import settings

        block_at = self._block_at or settings.AGENT_PLUGIN_SCAN_BLOCK_AT
        if self._file_cap_bytes is not None:
            cap = self._file_cap_bytes
        else:
            cap = settings.AGENT_PLUGIN_SCAN_FILE_CAP_MB * 1024 * 1024
        return ContentScanner(block_at=block_at, file_cap_bytes=cap).scan(package_root)
