## REMOVED Requirements

### Requirement: MCPClient connects to MCP servers for tool discovery
**Reason**: MCPClient and MCPManager specs are unrelated to the security layer change. They were bundled in security-mcp spec but belong to a different capability domain. No behavior change — only spec reorganization.
**Migration**: MCPClient and MCPManager requirements remain in the existing `openspec/specs/security-mcp/spec.md` and are not affected.

### Requirement: MCPManager manages multiple MCP connections
**Reason**: Same as above — unrelated to security layer change.
**Migration**: No migration needed. Existing spec remains unchanged.

### Requirement: MCPToolSync converts MCP tools to Hecate format
**Reason**: Same as above — unrelated to security layer change.
**Migration**: No migration needed. Existing spec remains unchanged.

## MODIFIED Requirements

### Requirement: LLMGuardScanner provides input/output safety scanning
The `LLMGuardScanner` SHALL scan prompts and outputs for safety issues using LLM Guard with lazy-loaded scanners and mock fallback. It SHALL also support returning the sanitized text from scanners (not just a boolean), enabling the SANITIZE action to carry transformed data.

#### Scenario: Scanner disabled
- **WHEN** `enabled=False` or `LLM_GUARD_ENABLED=False`
- **THEN** `scan_prompt()` and `scan_output()` SHALL return `ScanResult(is_safe=True, score=1.0, issues=[], sanitized_text=None)`

#### Scenario: Mock scanner when llm_guard not installed
- **WHEN** llm_guard is not installed
- **THEN** the scanner SHALL use mock scan that detects "hack" and "exploit" keywords

#### Scenario: Prompt scanners
- **WHEN** llm_guard is installed
- **THEN** prompt scanning SHALL use PromptInjection (threshold=0.5), Anonymize, and Secrets scanners

#### Scenario: Output scanners
- **WHEN** llm_guard is installed
- **THEN** output scanning SHALL use Toxicity scanner (threshold=0.7)

#### Scenario: Scan returns issues
- **WHEN** a scanner detects a risk
- **THEN** the result SHALL include the scanner name and risk score in issues list

#### Scenario: Scan returns sanitized text
- **WHEN** the Anonymize scanner processes text with PII
- **THEN** the `ScanResult` SHALL include `sanitized_text` containing the anonymized version

### Requirement: SecurityMiddleware orchestrates security scanning
The `SecurityMiddleware` SHALL orchestrate LLM Guard scanning for backward-compatible usage without importing NeMo Guardrails.

#### Scenario: Check input without NeMo
- **WHEN** `check_input(message)` is called
- **THEN** it SHALL call `LLMGuardScanner.scan_prompt()` only (no NeMo Guardrails call)

#### Scenario: Check output
- **WHEN** `check_output(output, prompt)` is called
- **THEN** it SHALL call `LLMGuardScanner.scan_output()` only
