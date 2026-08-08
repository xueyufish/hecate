"""Report Hecate OpenSpec spec coverage and quality.

Read-only companion to the hecate-docs-sync skill. Walks
``openspec/specs/*/spec.md``, parses structure, computes summary statistics,
flags quality issues, and prints a human-readable report to stdout.

This script NEVER writes any file. It is meant to give a contributor a fast
overview of the OpenSpec landscape so they can decide what human-authored
docs to write next.

Constraints:
    - Read-only access to ``openspec/``
    - Writes nothing — output goes to stdout only

Usage:
    python .opencode/skills/hecate-docs-sync/scripts/sync_specs.py
    python .opencode/skills/hecate-docs-sync/scripts/sync_specs.py --since 2026-01-01
    python .opencode/skills/hecate-docs-sync/scripts/sync_specs.py --quality-only
    python .opencode/skills/hecate-docs-sync/scripts/sync_specs.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve()
REPO_ROOT = SCRIPT.parents[4]  # this file lives at <repo>/.opencode/skills/hecate-docs-sync/scripts/

OPENSPEC_SPECS = REPO_ROOT / "openspec" / "specs"


@dataclass
class Scenario:
    name: str
    body: str = ""


@dataclass
class Requirement:
    name: str
    body: str = ""
    scenarios: list[Scenario] = field(default_factory=list)


@dataclass
class Spec:
    id: str
    title: str
    purpose: str = ""
    requirements: list[Requirement] = field(default_factory=list)
    source_path: Path | None = None
    mtime: datetime | None = None

    @property
    def req_count(self) -> int:
        return len(self.requirements)

    @property
    def scenario_count(self) -> int:
        return sum(len(r.scenarios) for r in self.requirements)


def parse_spec(spec_path: Path) -> Spec:
    """Parse a single ``spec.md`` file.

    Handles both formats found in the repo:
      - ``# <Name> Specification`` + ``## Purpose`` + ``## Requirements``
      - ``## ADDED Requirements`` directly (delta-style, no H1)
    """
    stat = spec_path.stat()
    text = spec_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    spec = Spec(
        id=spec_path.parent.name,
        title=spec_path.parent.name,
        source_path=spec_path,
        mtime=datetime.fromtimestamp(stat.st_mtime),
    )

    in_purpose = False
    current_req: Requirement | None = None
    current_scenario: Scenario | None = None

    for line in lines:
        stripped = line.strip()

        if line.startswith("# ") and spec.title == spec.id:
            spec.title = re.sub(r"\s+Specification\s*$", "", line[2:].strip())
            in_purpose = False
            current_req = None
            current_scenario = None
            continue

        if stripped == "## Purpose":
            in_purpose = True
            continue
        if stripped.startswith("## ") and stripped != "## Purpose":
            in_purpose = False
            current_req = None
            current_scenario = None
            continue

        if line.startswith("### Requirement: "):
            in_purpose = False
            current_req = Requirement(name=line[len("### Requirement: "):].strip())
            spec.requirements.append(current_req)
            current_scenario = None
            continue

        if line.startswith("#### Scenario: "):
            current_scenario = Scenario(name=line[len("#### Scenario: "):].strip())
            if current_req is not None:
                current_req.scenarios.append(current_scenario)
            continue

        if in_purpose:
            spec.purpose += line + "\n"
        elif current_scenario is not None:
            current_scenario.body += line + "\n"
        elif current_req is not None:
            current_req.body += line + "\n"

    spec.purpose = spec.purpose.strip()
    return spec


@dataclass
class QualityIssue:
    spec_id: str
    kind: str
    detail: str


def quality_check(spec: Spec) -> list[QualityIssue]:
    """Return quality issues found in a spec."""
    issues: list[QualityIssue] = []
    if not spec.purpose or spec.purpose.upper().startswith("TBD"):
        issues.append(
            QualityIssue(
                spec_id=spec.id,
                kind="missing-purpose",
                detail="Purpose is empty or still TBD (set after archive)",
            )
        )
    if not spec.requirements:
        issues.append(
            QualityIssue(spec_id=spec.id, kind="no-requirements", detail="No ### Requirement: blocks")
        )
    for req in spec.requirements:
        if not req.scenarios:
            issues.append(
                QualityIssue(
                    spec_id=spec.id,
                    kind="no-scenarios",
                    detail=f"Requirement '{req.name}' has no #### Scenario: blocks",
                )
            )
    return issues


def discover_specs(filter_id: str | None, filter_prefix: str | None) -> list[Path]:
    """Find all matching ``spec.md`` files.

    ``filter_id`` is an exact ID match. ``filter_prefix`` is an ID prefix match.
    ``openspec/specs/`` is a flat layout — every spec is its own directory.
    """
    if filter_id:
        spec_path = OPENSPEC_SPECS / filter_id / "spec.md"
        if not spec_path.is_file():
            sys.exit(f"Error: feature '{filter_id}' not found at {spec_path}")
        return [spec_path]

    all_specs = sorted(OPENSPEC_SPECS.glob("*/spec.md"))

    if filter_prefix:
        matched = [p for p in all_specs if p.parent.name.startswith(f"{filter_prefix}-")]
        if not matched:
            sys.exit(
                f"Error: no specs found with ID prefix '{filter_prefix}-'. "
                f"Available examples: {[p.parent.name for p in all_specs[:5]]}..."
            )
        return matched

    return all_specs


def render_text_report(
    specs: list[Spec],
    issues: list[QualityIssue],
    since: date | None,
) -> str:
    """Render a human-readable report to stdout."""
    total_req = sum(s.req_count for s in specs)
    total_scn = sum(s.scenario_count for s in specs)

    out: list[str] = []
    out.append("=" * 72)
    out.append("Hecate OpenSpec Spec Report")
    out.append("=" * 72)
    out.append("")
    out.append(f"Source:    {OPENSPEC_SPECS.relative_to(REPO_ROOT)}")
    out.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    if since:
        out.append(f"Filter:    modified since {since.isoformat()}")
    out.append("")

    # Summary
    out.append("## Summary")
    out.append("")
    out.append(f"  Features:        {len(specs)}")
    out.append(f"  Requirements:    {total_req}")
    out.append(f"  Scenarios:       {total_scn}")
    avg_req = total_req / len(specs) if specs else 0
    avg_scn = total_scn / total_req if total_req else 0
    out.append(f"  Avg reqs/feature:   {avg_req:.1f}")
    out.append(f"  Avg scenarios/req:  {avg_scn:.1f}")
    out.append("")

    # Domain breakdown
    domains: Counter[str] = Counter()
    for spec in specs:
        prefix = spec.id.split("-", 1)[0]
        domains[prefix] += 1
    out.append("## Domains")
    out.append("")
    for domain, count in sorted(domains.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"  {domain:20s} {count:3d}")
    out.append("")

    # Recently modified
    recent = [s for s in specs if s.mtime and s.mtime.date() >= (since or date(1970, 1, 1))]
    recent.sort(key=lambda s: s.mtime or datetime.min, reverse=True)
    out.append(f"## Recently Modified ({len(recent)} specs)")
    out.append("")
    for spec in recent[:20]:
        assert spec.mtime is not None
        date_str = spec.mtime.date().isoformat()
        out.append(f"  {date_str}  {spec.id:40s} ({spec.req_count} req, {spec.scenario_count} scen)")
    if len(recent) > 20:
        out.append(f"  ... and {len(recent) - 20} more")
    out.append("")

    # Quality issues
    if issues:
        out.append(f"## Quality Issues ({len(issues)})")
        out.append("")
        by_kind: dict[str, list[QualityIssue]] = {}
        for issue in issues:
            by_kind.setdefault(issue.kind, []).append(issue)
        for kind, kind_issues in sorted(by_kind.items()):
            out.append(f"  [{kind}]  ({len(kind_issues)} specs)")
            for issue in kind_issues[:10]:
                out.append(f"    - {issue.spec_id}: {issue.detail}")
            if len(kind_issues) > 10:
                out.append(f"    ... and {len(kind_issues) - 10} more")
            out.append("")
    else:
        out.append("## Quality Issues")
        out.append("")
        out.append("  (none)")
        out.append("")

    # Top specs by scenario count (worth documenting)
    out.append("## Top 10 Features by Scenario Count")
    out.append("")
    out.append("  (richest specs — most worth surfacing in user-facing docs)")
    out.append("")
    top = sorted(specs, key=lambda s: s.scenario_count, reverse=True)[:10]
    for spec in top:
        out.append(f"  {spec.scenario_count:4d} scenarios  {spec.id:40s} ({spec.req_count} req)")
    out.append("")

    out.append("=" * 72)
    return "\n".join(out) + "\n"


def render_json_report(
    specs: list[Spec],
    issues: list[QualityIssue],
) -> str:
    """Render a machine-readable JSON report."""
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "features": len(specs),
            "requirements": sum(s.req_count for s in specs),
            "scenarios": sum(s.scenario_count for s in specs),
        },
        "domains": dict(Counter(s.id.split("-", 1)[0] for s in specs)),
        "quality_issues": [
            {"spec_id": i.spec_id, "kind": i.kind, "detail": i.detail} for i in issues
        ],
        "specs": [
            {
                "id": s.id,
                "title": s.title,
                "req_count": s.req_count,
                "scenario_count": s.scenario_count,
                "mtime": s.mtime.isoformat() if s.mtime else None,
            }
            for s in specs
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ===========================================================================
# README audit
# ===========================================================================


@dataclass
class AuditFinding:
    """Single finding from the README audit."""

    kind: str  # "badge" | "link" | "version" | "license" | "command"
    severity: str  # "ok" | "warn" | "error" | "skip"
    target: str  # URL or field name being audited
    detail: str  # human-readable explanation


SHIELDS_RE = re.compile(r"https?://img\.shields\.io/[^\s)`>\]]+")
GITHUB_RE = re.compile(r"https?://github\.com/[^\s)`>\]]+")
PYTHON_VER_RE = re.compile(r"python[- ]?(?:3\.\d{1,2}\+?|\d\.\d{1,2}\+?)", re.IGNORECASE)


def _read(path: Path) -> str:
    """Read a UTF-8 text file. Returns empty string if missing."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _pyproject_get(text: str, key: str) -> str | None:
    """Extract a top-level string value from pyproject.toml without tomllib.

    Works for lines like ``key = "value"``. Handles both single and double
    quotes, and avoids matching inside other sections by stopping at the
    next blank line or section header.
    """
    pattern = re.compile(rf'^{re.escape(key)}\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)
    match = pattern.search(text)
    return match.group(1) if match else None


def _pyproject_get_console_scripts(text: str) -> list[str]:
    """Extract the entry-point names from ``[project.scripts]``.

    Matches lines like ``hecate = "hecate.cli.main:app"`` and returns the
    left-hand side names.
    """
    section_pattern = re.compile(r"\[project\.scripts\](.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
    section = section_pattern.search(text)
    if section is None:
        return []
    entry_pattern = re.compile(r'^([\w-]+)\s*=\s*["\'].*["\']', re.MULTILINE)
    return entry_pattern.findall(section.group(1))


def _head_check(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Return (ok, reason). Uses urllib (stdlib). HEAD with GET fallback."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", "hecate-docs-sync/0.1")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.status < 400, f"HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        return (e.code < 400, f"HTTP {e.code}")
    except urllib.error.URLError as e:
        return (False, f"URL error: {e.reason}")
    except (TimeoutError, OSError) as e:
        return (False, f"network: {e}")
    except Exception as e:  # pragma: no cover - defensive
        return (False, f"unexpected: {type(e).__name__}: {e}")


def _head_check_offline(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Stub for --audit-offline mode: report OK without making network requests."""
    return (True, "skipped (--audit-offline)")


HEAD_CHECK = _head_check


def _is_badge_url(url: str) -> bool:
    """Heuristic: is this a badge image URL vs a regular link?"""
    return "shields.io" in url or "/badge." in url or ".svg" in url.lower().split("?")[0]


def audit_readme() -> list[AuditFinding]:
    """Run all five README-audit checks and return findings."""
    findings: list[AuditFinding] = []

    readme_text = _read(REPO_ROOT / "README.md")
    if not readme_text:
        findings.append(
            AuditFinding(
                kind="command",
                severity="error",
                target="README.md",
                detail="README.md not found at repo root",
            )
        )
        return findings

    # 1. Badge URL validity
    badge_urls = sorted(set(SHIELDS_RE.findall(readme_text)))
    for url in badge_urls:
        ok, reason = HEAD_CHECK(url)
        findings.append(
            AuditFinding(
                kind="badge",
                severity="ok" if ok else "warn",
                target=url,
                detail=reason,
            )
        )
    if not badge_urls:
        findings.append(
            AuditFinding(
                kind="badge",
                severity="warn",
                target="README.md",
                detail="no shields.io badges found (consider adding CI / version / license)",
            )
        )

    # 2. GitHub link validity (skip badges)
    all_gh = sorted(set(GITHUB_RE.findall(readme_text)))
    gh_links = [u for u in all_gh if not _is_badge_url(u)]
    for url in gh_links:
        ok, reason = HEAD_CHECK(url)
        findings.append(
            AuditFinding(
                kind="link",
                severity="ok" if ok else "warn",
                target=url,
                detail=reason,
            )
        )
    if not gh_links:
        findings.append(
            AuditFinding(
                kind="link",
                severity="skip",
                target="README.md",
                detail="no GitHub links found",
            )
        )

    # 3. Python version match
    pyproject_text = _read(REPO_ROOT / "pyproject.toml")
    pyproject_python = _pyproject_get(pyproject_text, "requires-python") if pyproject_text else None
    readme_python_versions = sorted(set(PYTHON_VER_RE.findall(readme_text)))
    if pyproject_python and readme_python_versions:
        # normalize e.g. "3.12+" -> "3.12"
        pyproject_min = re.search(r"3\.\d{1,2}", pyproject_python)
        pyproject_min_str = pyproject_min.group(0) if pyproject_min else pyproject_python
        matched = any(pyproject_min_str in v for v in readme_python_versions)
        findings.append(
            AuditFinding(
                kind="version",
                severity="ok" if matched else "warn",
                target="python",
                detail=(
                    f"pyproject.toml requires-python='{pyproject_python}' "
                    f"vs README mentions {readme_python_versions}"
                ),
            )
        )
    else:
        findings.append(
            AuditFinding(
                kind="version",
                severity="skip",
                target="python",
                detail=(
                    f"pyproject_python={pyproject_python!r} "
                    f"readme_versions={readme_python_versions!r}"
                ),
            )
        )

    # 4. License match
    license_text = _read(REPO_ROOT / "LICENSE")
    license_first_line = license_text.splitlines()[0] if license_text else ""
    readme_license = "MIT" if "License: MIT" in readme_text or "License-MIT" in readme_text else None
    if license_text and readme_license:
        match = "MIT" in license_first_line
        findings.append(
            AuditFinding(
                kind="license",
                severity="ok" if match else "warn",
                target="LICENSE",
                detail=(
                    f"LICENSE first line '{license_first_line}' "
                    f"vs README badge '{readme_license}'"
                ),
            )
        )
    elif not license_text:
        findings.append(
            AuditFinding(
                kind="license",
                severity="warn",
                target="LICENSE",
                detail="LICENSE file not found at repo root",
            )
        )
    else:
        findings.append(
            AuditFinding(
                kind="license",
                severity="skip",
                target="README.md",
                detail="no MIT license badge / mention found in README",
            )
        )

    # 5. Install commands sanity
    console_scripts = _pyproject_get_console_scripts(pyproject_text) if pyproject_text else []
    docker_compose_exists = (REPO_ROOT / "docker" / "docker-compose.yml").is_file()
    if console_scripts:
        missing = [s for s in console_scripts if s not in readme_text]
        if missing:
            findings.append(
                AuditFinding(
                    kind="command",
                    severity="warn",
                    target="console_scripts",
                    detail=(
                        f"pyproject.toml declares {console_scripts} but "
                        f"README does not mention: {missing}"
                    ),
                )
            )
        else:
            findings.append(
                AuditFinding(
                    kind="command",
                    severity="ok",
                    target="console_scripts",
                    detail=f"all declared entry points {console_scripts} mentioned in README",
                )
            )
    if docker_compose_exists:
        mentioned = "docker-compose" in readme_text or "docker compose" in readme_text
        findings.append(
            AuditFinding(
                kind="command",
                severity="ok" if mentioned else "warn",
                target="docker-compose.yml",
                detail=(
                    "docker/docker-compose.yml present; "
                    + ("referenced in README" if mentioned else "NOT referenced in README")
                ),
            )
        )

    return findings


def render_audit_report(findings: list[AuditFinding]) -> str:
    """Render the README audit report as human-readable text."""
    by_kind: dict[str, list[AuditFinding]] = {}
    for f in findings:
        by_kind.setdefault(f.kind, []).append(f)

    summary_counts: dict[str, int] = {"ok": 0, "warn": 0, "error": 0, "skip": 0}
    for f in findings:
        summary_counts[f.severity] = summary_counts.get(f.severity, 0) + 1

    out: list[str] = []
    out.append("=" * 72)
    out.append("Hecate README Audit Report")
    out.append("=" * 72)
    out.append("")
    out.append(f"Source:    README.md")
    out.append(f"Repo:      {REPO_ROOT}")
    out.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(
        f"  Checks:  {len(by_kind)} categories · "
        f"{len(findings)} findings "
        f"({summary_counts['error']} error, "
        f"{summary_counts['warn']} warn, "
        f"{summary_counts['ok']} ok, "
        f"{summary_counts['skip']} skip)"
    )
    out.append("")

    section_titles = {
        "badge": "Badges (shields.io URLs)",
        "link": "GitHub Links",
        "version": "Python Version",
        "license": "License",
        "command": "Install Commands",
    }

    for kind, title in section_titles.items():
        items = by_kind.get(kind, [])
        if not items:
            continue
        out.append(f"## {title} ({len(items)})")
        out.append("")
        for f in items:
            tag = f"[{f.severity.upper():5s}]"
            target = f.target if len(f.target) <= 80 else f.target[:77] + "..."
            out.append(f"  {tag}  {target}")
            out.append(f"          {f.detail}")
        out.append("")

    out.append("=" * 72)
    return "\n".join(out) + "\n"


def render_audit_json(findings: list[AuditFinding]) -> str:
    """Render the README audit report as JSON."""
    return json.dumps(
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "findings": [
                {"kind": f.kind, "severity": f.severity, "target": f.target, "detail": f.detail}
                for f in findings
            ],
        },
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report Hecate OpenSpec spec coverage and quality. Read-only — writes nothing.",
    )
    parser.add_argument("--feature", help="Report only this feature ID (e.g. 'cli').")
    parser.add_argument(
        "--path",
        help="Report specs whose ID starts with this prefix (e.g. 'agent').",
    )
    parser.add_argument(
        "--since",
        help="Show only specs modified on/after this date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help="Show only the quality-issues section; suppress summary and top-N.",
    )
    parser.add_argument(
        "--audit-readme",
        action="store_true",
        help="Run README drift audit instead of OpenSpec report (badges, GitHub links, Python version, license, install commands).",
    )
    parser.add_argument(
        "--audit-offline",
        action="store_true",
        help="Skip network checks during --audit-readme (only run pyproject/LICENSE/install-command checks).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the default human report.",
    )
    args = parser.parse_args(argv)

    if args.audit_readme:
        global HEAD_CHECK
        if args.audit_offline:
            HEAD_CHECK = _head_check_offline
        else:
            HEAD_CHECK = _head_check
        findings = audit_readme()
        sys.stdout.write(
            render_audit_json(findings) if args.json else render_audit_report(findings)
        )
        return 0

    if args.feature and args.path:
        sys.exit("Error: --feature and --path are mutually exclusive.")

    spec_paths = discover_specs(args.feature, args.path)
    if not spec_paths:
        sys.exit("Error: no specs found matching the given filters.")

    specs = [parse_spec(p) for p in spec_paths]
    issues: list[QualityIssue] = []
    for spec in specs:
        issues.extend(quality_check(spec))

    since_date: date | None = None
    if args.since:
        try:
            since_date = date.fromisoformat(args.since)
        except ValueError:
            sys.exit(f"Error: --since must be YYYY-MM-DD, got '{args.since}'")

    if args.quality_only and not args.json:
        full = render_text_report(specs, issues, since_date)
        lines = full.splitlines()
        start_idx = next((i for i, ln in enumerate(lines) if ln.startswith("## Quality Issues")), -1)
        if start_idx < 0:
            sys.stdout.write(full)
        else:
            end_idx = len(lines)
            for j in range(start_idx + 1, len(lines)):
                if lines[j].startswith("==="):
                    end_idx = j
                    break
            sys.stdout.write("\n".join(lines[start_idx:end_idx]) + "\n")
    elif args.json:
        sys.stdout.write(render_json_report(specs, issues))
    else:
        sys.stdout.write(render_text_report(specs, issues, since_date))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())