# Proposal: plugin-content-scanning (5.13a)

## Why

5.5c（Agent Plugins 1.0 Ingestion）已于 2026-08-18 ship，但其总开关 `AGENT_PLUGINS_INGESTION_ENABLED` 默认关闭——本 feature 是该开关的 go-live 硬门：现有 spec 明文约定 scan stage "reserved, null until feature 5.13a"。不落地扫描器，第三方插件生态就不能开放。

攻击面证据充分：Snyk ToxicSkills 扫描 3,984 个 ClawHub skill，36.82% 含缺陷、13.4% critical、76 个确认恶意（注入模式存在于 91% 的恶意 skill）；ClawHavoc 战役 341 个恶意 skill 以混淆手法（base64 编码的 `curl|bash`）实际绕过了自动化扫描器；MCP 官方 registry 预览期明确不审计——过滤责任落在客户端 ingest 层。

2026-08 竞品调研（17+ 平台）显示业界已分裂为两阵营：做 ingestion 时确定性内容扫描的一方（DeerFlow SkillScan、Hermes-agent、Dify 发布侧扫描、Google Gemini Enterprise "Governing Agent Skills"、ClawHub 发布侧 VirusTotal、FortiCNAPP Skills Scanning、IBM watsonx 脚本 deny-list）收敛到同一架构模式：**确定性规则先行 → CRITICAL fail-closed → warn 可人工豁免 → LLM 二遍可选**，与本 feature 的既定规划（v1 纯规则引擎、v2 可选 LLM 二遍）完全吻合。华为 AgentArts/openJiuwen（同源）、AgentScope、CatPaw、Bedrock AgentCore、Manus、Palantir、deepseek-harness 均无安装时内容扫描。

## What Changes

- 新增规则引擎扫描器（`plugin/content_scanner.py`），实现 5.5c 预留的 `ScanStage` 协议，替换 no-op 实现；`plugin/agent_plugins.py` 协议定义不动
- 四类检测：注入模式（regex + 启发式）、隐形 Unicode 走私（零宽/bidi/tag 字符，含 run 长度阈值）、秘密检测、`allowed-tools` 预授权审计；外加高置信 URL 检测（paste-site 域名、IP 字面量、同形字域名）
- 混淆解码层 v1 范围：NFKC 归一化前处理 + 有界 base64/hex 解码重扫；finding schema 预留 `transform` 字段保证前向兼容（完整 confusables 表与实体解码推迟 v1.1）
- 按文件角色（frontmatter/body/mcp.json/嵌套支撑文件/README 等）× 规则固有严重度的定级矩阵；verdict = severity 对 `AGENT_PLUGIN_SCAN_BLOCK_AT`（默认 `high`）的切线，三档 allow/warn/block
- fail-closed 语义：block 裁决拒绝安装；扫描器自身异常拒绝安装（不 fail-open）；超过单文件扫描上限（1MB 文本）产生 finding 而非静默跳过（针对 22MB 填充逃逸攻击）；二进制文件按类型跳过且不产生 finding
- enable 时重扫：覆盖规则演进（`scanner_version` 变化）与存量回填（`scan_result` 为 null 的 5.5c 时期包）；投影按 `(content_hash, scanner_version)` 幂等去重
- warn 档管理员确认（ack）抑制：同 `content_hash` + 同 `rule_id` 的已确认命中在后续重扫中降级，避免告警疲劳；复用 `SecurityFindingService.set_feedback` 机制
- 新端点 `GET /api/plugins/{id}/scan`；扫描发现投影到 `SecurityFindingModel` 供 Ops Center 展示（含被 block 的安装尝试）
- go-live：`AGENT_PLUGINS_INGESTION_ENABLED` 默认值翻转为 `true`（此后兼作应急 kill-switch）

无 breaking change：总开关翻转前新代码路径不可达；翻转后行为变化即为本 feature 的交付语义本身。

## Capabilities

### New Capabilities

- `plugin-content-scanning`: 安装/启用时的插件内容扫描——规则引擎与规则集（注入/隐形 Unicode/秘密/allowed-tools/URL）、混淆解码层、按文件角色的严重度矩阵与 verdict 计算、fail-closed 执行语义、finding schema 与证据脱敏、enable 重扫与幂等投影、ack 抑制、扫描结果查询 API、相关配置项

### Modified Capabilities

- `agent-plugins-ingestion`: "Scan stage slot" 需求从 no-op 占位改为真实执行——block 裁决可拒绝安装；`scan_result` 不再恒为 null；enable 重扫语义从协议预留变为落地行为；go-live 门（"no-op 阶段不阻断安装"条款）随总开关翻转撤销

## Impact

- **代码**：`src/hecate/plugin/content_scanner.py`（新增）；`services/plugin/service.py`（install 接线 + enable 重扫 + verdict 强制）；`api/management/plugins.py`（`GET /{id}/scan`）；`services/security/finding_service.py`（投影与 ack，复用为主）；`core/config.py`（`AGENT_PLUGIN_SCAN_BLOCK_AT` 等新配置 + 总开关翻转）
- **依赖**：无新增第三方依赖——NFKC/regex 为标准库；秘密检测基于内置规则集（可选联动既有 detect-secrets 包装，见 design）
- **数据库**：无新迁移——`PluginModel.scan_result` 与 `SecurityFindingModel` 均已存在
- **文档**：`docs/features/feature-catalog.md` 与 `roadmap.md` 的 5.13a 条目补入调研新增参照（Google GE Governing Agent Skills、DeerFlow SkillScan、Hermes-agent、FortiCNAPP SK-* 分类法）；openJiuwen 与 AgentArts 同源事实记入
- **运营**：开关翻转后自托管部署获得第三方插件安装能力；SaaS 模式行为不变（stdio 已 default-deny，扫描是叠加防线）
