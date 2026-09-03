"""DLP package — Data Loss Prevention engine for Hecate.

Provides unified scanning across all four trust boundaries (PreLLM,
PostLLM, PreTool, PostTool) plus the dedicated egress gate for MCP
responses. The package follows the three-layer industry standard
(Detection → Policy → Enforcement) as recorded in design.md §D1.
"""

from __future__ import annotations
