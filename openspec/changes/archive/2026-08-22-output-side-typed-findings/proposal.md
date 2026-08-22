## Why

P3 收尾 4 项中的 2 项(9.1a Injection Type Detection、9.2 System Prompt Leakage Protection)长期未交付,导致 catalog 行 "P3 closes at 4 items" 无法归零。深入探索后发现现状比 catalog 假设更糟:

- **9.1a / 9.2 完全没接上**:`OutputSecurityHook` 现有实现只跑 toxicity + PII deanonymize + DLP scan 三段,没有 injection 检测或 prompt 泄漏检测。
- **`security_finding_writer` 是悬空线**:`OutputSecurityHook.__init__` 接受该参数,`_write_audit_records` 也会调用它,但 `create_security_hooks` 与 `assemble_guardrails` **都没传**。即 DLP today 在 output 侧能 block/mask,findings 不落库 —— `SecurityFindingModel` 仅由 `services/audit/writer.py` 通过 audit log 反向触发,DLP output findings 缺席 SIEM 通道。
- **行业基准对齐缺位**:Amazon Bedrock Guardrails Standard tier 的 "Prompt Attack" + "code elements 保护" 已逐字对应我们 ADR-026 里 4 类 sink;DeerFlow 的 SkillScan(确定性 + LLM 双层)+ "Active HTML/XSS artifact forced download at Gateway boundary" 给出最贴近参考实现;OWASP LLM07:2025 给出 4 条防护建议与 4 个 example 攻击类型,我们的 ADR 仅覆盖其中 1.5 条。

业界调研(`docs/research/2026-08-output-guardrails-comparison.md`,本次探索同步产出)对比矩阵显示:在 9.1a / 9.2 这两个具体检测点上,**没有成熟项目直接覆盖**;最贴近形态是 Bedrock Guardrails Standard tier + DeerFlow SkillScan;Amazon Bedrock 的 `ApplyGuardrail` API 与 NeMo Guardrails 的 Colang DSL 都给了不同形态参照。本变更采纳"regex 注册表 + 同构 DLP Recognizer 架构"方案,理由是零新依赖、与现有 DLP 子系统架构一致、与 guardrail-upgrade-trio 既有中间件链兼容。

## What Changes

按三层推进,前一层是后一层的接线/契约地基:

- **L0 接通悬空 findings 通道(9.14 enhancement,共担底座)**:把 `security_finding_writer` 从 `OutputSecurityHook.__init__` 的"接受但不传"状态接通到生产路径 —— `create_security_hooks` 工厂接受可选 `finding_writer` 参数,`assemble_guardrails` 在事件存储可用时实例化并注入。配置面新增 `output_findings` section(默认 enable,向后兼容)。DLP output findings 自动落 `SecurityFindingModel`,通过既有 SIEM collector(8.7 SS5)推送到 Webhook/Syslog/OCSF,**不改 SIEM 模块代码**。
- **L1 9.1a Injection Type Detection**:4 类 recognizer 落地 —— code_python / sql / template_jinja / xss。**默认 action 为 AUDIT**(只产生 findings 不拦截),per-type 可配置为 BLOCK / MASK / SANITIZE。算法:regex 注册表(zero new dependency,与 `services/security/dlp/recognizers/` 同构)。自定义规则通过配置注入(`custom_patterns`),不引入 YARA 语法。Bedrock Standard tier "code elements"思路借鉴为 context-aware 启发式(可选开关,默认关闭)。
- **L2 9.2 System Prompt Leakage Protection**:基于 winnowing n-gram 指纹(零依赖、可在 CI 跑基准),阈值默认 0.20(>20% system prompt 内容复现即触发)。action 默认 BLOCK,可配 SANITIZE(剥除复现片段)。system prompt 指纹来源:PostLLMHook 收到的 `messages[0]`(已确认两条执行路径都把 system 消息放在首位 —— `agent_execution_port.py:110` 与 Pregel LLMWorker 同构)。严重度分级:复现 persona/泛描述 → LOW;复现安全规则/角色权限 → HIGH;复现 embedded secrets/API key → CRITICAL(后者与 DLP secrets recognizer 联动,避免双写)。embedding 语义相似度留 seam,本变更不实现。

**BREAKING**:无 API 层面 breaking;`create_security_hooks` 签名扩参数(向后兼容,所有现有 kwargs 保持默认);`OutputSecurityHook.__init__` 扩 kwargs(向后兼容);事件类型增量添加 2 个(INJECTION_DETECTED / PROMPT_LEAKAGE_DETECTED),EventType 已有 CUSTOM 回落兼容。

## Capabilities

### New Capabilities

- `injection-detection`: 9.1a 输出侧注入类型检测 —— regex recognizer 注册表(4 类内置 + custom_patterns 扩展位)、per-type action 配置(AUDIT/BLOCK/MASK/SANITIZE)、findings 落库、context-aware 启发式 seam。
- `prompt-leakage-protection`: 9.2 系统提示泄漏防护 —— winnowing 指纹算法、阈值配置、action 配置(BLOCK/SANITIZE)、severity 分级(LOW/HIGH/CRITICAL)、与 DLP secrets recognizer 联动。
- `output-findings-wiring`: 共担底座 —— `security_finding_writer` 接通生产路径、`OutputSecurityHook` 配置面扩 `output_findings` section、向后兼容(SIEM 通道自动消化,无需新代码)。

## Impact

- **代码**:
  - 新增 `services/security/output/injection_detection/`(recognizer 基类、4 类内置 recognizer、scanner facade)
  - 新增 `services/security/output/prompt_leakage/`(fingerprint、scanner、sanitizer)
  - 改 `services/security/hooks/output_security.py`(扩展 __init__ kwargs、新增 `_check_injection`、`_check_prompt_leakage` 步骤、与现有 DLP scan 共用 `_write_audit_records`)
  - 改 `services/security/hooks/__init__.py`(扩 `create_security_hooks` kwargs)
  - 改 `services/security/guardrail_assembly.py`(`assemble_guardrails` 接受 `finding_writer` 参数并透传)
  - 改 `engine/eventstore.py`(增量 EventType.INJECTION_DETECTED / EventType.PROMPT_LEAKAGE_DETECTED,带向后兼容注释)
  - 改 `services/security/finding_service.py`(rule_name 白名单扩展支持新规则)
- **API**:无 REST 接口变更。
- **事件**:2 个新 EventType,增量扩展,EventType 既有 CUSTOM 回落兼容契约保持(ADR-030 §1)。
- **依赖**:零新增第三方依赖(regex / hashlib / stdlib 足够)。
- **测试**:`test_services/test_security_hooks_injection.py`、`test_services/test_security_hooks_prompt_leakage.py`、`test_services/test_security_finding_wiring.py`(新增);既有 `test_services/test_security_hooks.py` / `test_services/test_guardrail_assembly.py` 扩 case。
- **文档**:`docs/design/security-architecture.md` SS3/SS4 段补完(原文档仅一段话);`docs/design/adr/026-security-shield-enhancement.md` 增补 OWASP LLM07 4 条防护建议映射;`docs/features/feature-catalog.md` 把 9.1a / 9.2 从 "P3 close-out items" 移除,标 ✅;`docs/features/positioning.md` catalog 与 roadmap 同步(AGENTS.md 强制要求)。

## Non-Goals(明确不在本变更)

- **sink-aware 注入检测**(基于 PreToolHook 在 tool arg = LLM output 的点上加 sink 元信息):留 seam(给 detector 预留 `sink` 参数),但不接入数据。理由:PostLLMHook 当前无 sink 信息;扩展到 PreToolHook 需动 tool 链路,scope 膨胀。Deferred。
- **9.2 语义相似度(v2,embedding-based 改写型泄漏检测)**:留 seam(可选开关 `embedding_similarity_enabled`,默认 False)。Deferred。
- **Bedrock Standard tier "code elements context" 深度研究**:本次调研确认方向,但具体上下文启发式算法(注释/变量名/字符串字面量如何建模)留 follow-up。Deferred。
- **OpenClaw source-of-truth refresh**:Hecate `feature-catalog.md` 引用 OpenClaw 14 failure types / TypeBox / ClawHub,当前 github URL 已迁移或重定向到同名"space lobster"主题项目,需核实资料可靠性。**本仓库 issue,不在本变更 scope**,但影响后续所有 ADR 基准引用。Deferred。
- **Watsonx.ai / Vertex AI Agent Builder / ADK 安全特性补全调研**:竞争对标完整度,留 follow-up。Deferred。
- **路径 A chat 流式输出实时阻断**(streaming 下 token 已吐给客户端时的 9.2 拦截):业界默认接受 post-check 兜底(Bedrock ApplyGuardrail 同样如此),Hecate LLMWorker.execute_stream 行为对齐,不改。Deferred。

## Deferred to Follow-up Changes(本变更显式登记,后续 change 接力)

1. **9.1a sink-aware extension**:`injection-detection/spec.md` 预留 `sink` 参数与 `detect_with_sink(sink_type, content)` 函数签名,future change 在 PreToolHook 侧接入 sink 元数据并切换到 sink-aware 路径。
2. **9.2 semantic similarity v2**:`prompt-leakage-protection/spec.md` 预留 `embedding_similarity_enabled` 配置开关,future change 引入 embedding adapter(可选依赖 `[security]` group)并扩展 fingerprint matcher。
3. **output-side typed findings 适配层覆盖 streaming**:本变更仅覆盖非流式与流式累积后的 post hook 阶段;**未来 LLMWorker 引入 token-level 中段切窗**(类似 Bedrock chunk-level ApplyGuardrail)时,本变更的 detector 需提供 chunk 接口。
4. **OpenClaw 引用源核实**:Hecate `feature-catalog.md` 多处引用 OpenClaw(14 failure types / TypeBox / ClawHub / 6-layer filtering)需在独立 issue 下重新交叉验证源头资料,本次不展开。
5. **Watsonx / Vertex ADK / Palantir AIP / AgentArts / openJiuwen / Manus / CatPaw 安全特性补全**:本次调研仅确认这些项目未公开 9.1a / 9.2 同构能力,具体安全模块文档需进一步 deep dive,留待后续竞争对标完整度补全。

## Catalog Sync(归档时强制)

AGENTS.md 要求 `/opsx-archive` 前同步 `docs/design/positioning.md`:

- `docs/features/feature-catalog.md` 9.1a 行从 P3 close-out 移入 ✅ 列表;description 中 "Planned enhancement (SS3)" 段标记已交付。
- `docs/features/feature-catalog.md` 9.2 行同样同步。
- `docs/features/roadmap.md` P3 段 "Remaining 4 close-out items" 列表移除 9.1a / 9.2;P3 进度从 83/87 改为 85/87。
- `docs/design/security-architecture.md` §Injection Type Detection / §System Prompt Leakage 段落补完(OWASP LLM07 4 个 example + Bedrock Standard tier 对应 + 本变更落地点)。
- `docs/design/adr/026-security-shield-enhancement.md` SS3 / SS4 决策段补完(算法、默认值、配置面、seam)。