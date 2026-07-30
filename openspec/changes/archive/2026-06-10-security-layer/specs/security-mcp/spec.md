## REMOVED Requirements — 移除的需求

### 需求：MCPClient 连接到 MCP 服务器以发现工具
**原因**：MCPClient 和 MCPManager 规范与安全层变更无关。它们被捆绑在 security-mcp 规范中，但属于不同的能力域。行为无变化——仅规范重组
**迁移**：MCPClient 和 MCPManager 要求保留在现有 `openspec/specs/security-mcp/spec.md` 中，不受影响

### 需求：MCPManager 管理多个 MCP 连接
**原因**：同上——与安全层变更无关
**迁移**：无需迁移。现有规范保持不变

### 需求：MCPToolSync 将 MCP 工具转换为 Hecate 格式
**原因**：同上——与安全层变更无关
**迁移**：无需迁移。现有规范保持不变

## MODIFIED Requirements — 修改的需求

### 需求：LLMGuardScanner 提供输入/输出安全扫描
`LLMGuardScanner` 应使用 LLM Guard 扫描提示和输出以发现安全问题，带延迟加载扫描器和 mock 回退。它还应支持从扫描器返回清洗后文本（不仅仅是布尔值），使 SANITIZE 动作能够携带转换后的数据

#### 场景：扫描器已禁用
- **当** `enabled=False` 或 `LLM_GUARD_ENABLED=False`
- **则** `scan_prompt()` 和 `scan_output()` 应返回 `ScanResult(is_safe=True, score=1.0, issues=[], sanitized_text=None)`

#### 场景：未安装 llm_guard 时的 mock 扫描器
- **当** 未安装 llm_guard
- **则** 扫描器应使用检测"hack"和"exploit"关键词的 mock 扫描

#### 场景：提示扫描器
- **当** 已安装 llm_guard
- **则** 提示扫描应使用 PromptInjection（阈值=0.5）、Anonymize 和 Secrets 扫描器

#### 场景：输出扫描器
- **当** 已安装 llm_guard
- **则** 输出扫描应使用 Toxicity 扫描器（阈值=0.7）

#### 场景：扫描返回问题
- **当** 扫描器检测到风险
- **则** 结果应在问题列表中包含扫描器名称和风险分数

#### 场景：扫描返回清洗后文本
- **当** Anonymize 扫描器处理带 PII 的文本
- **则** `ScanResult` 应包含包含匿名化版本的 `sanitized_text`

### 需求：SecurityMiddleware 编排安全扫描
`SecurityMiddleware` 应编排 LLM Guard 扫描以向后兼容使用，而不导入 NeMo Guardrails

#### 场景：不带 NeMo 的输入检查
- **当** 调用 `check_input(message)`
- **则** 应仅调用 `LLMGuardScanner.scan_prompt()`（无 NeMo Guardrails 调用）

#### 场景：输出检查
- **当** 调用 `check_output(output, prompt)`
- **则** 应仅调用 `LLMGuardScanner.scan_output()`
