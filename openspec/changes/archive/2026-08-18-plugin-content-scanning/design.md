# Design: plugin-content-scanning (5.13a)

## Context

5.5c 已交付 Agent Plugins 1.0 的完整 ingest 管线,并在 `plugin/agent_plugins.py` 预留了扫描槽位:`ScanStage.scan(package_root) -> ScanResult | None` 协议 + `ScanResult(verdict, findings, scanner_version)` 数据类 + `PluginModel.scan_result` JSON 列 + 安装管线调用点(services/plugin/service.py,位于校验与持久化之间)。当前为 no-op 实现,`scan_result` 恒 null;总开关 `AGENT_PLUGINS_INGESTION_ENABLED` 默认关,go-live 门由本 feature 承接。

业界调研结论(2026-08,详见 proposal):确定性规则先行 → CRITICAL fail-closed → warn 可人工豁免 → LLM 二遍可选,是做内容扫描阵营(DeerFlow SkillScan、Hermes-agent、Dify、Google GE Governing Agent Skills、FortiCNAPP)的收敛模式,与本设计一致。攻击实证:ClawHavoc 以 base64 混淆绕过扫描器;22MB 填充 README 绕过 VirusTotal/ClawScan 体积阈值;Embrace the Red 给出隐形 Unicode 的具体阈值(tag run >10 codepoints = critical,全文可疑 codepoint >100)。

## Goals / Non-Goals

**Goals:**

- 把 `ScanStage` 的 no-op 实现替换为确定性规则引擎,接上 go-live 门
- 四类检测 + 混淆解码层 + 按文件角色的严重度矩阵 + fail-closed 三档裁决
- enable 重扫(规则演进 + 存量回填)、ack 抑制、Ops Center 投影、扫描结果 API

**Non-Goals:**

- LLM 二遍审查(v2,org 可选增强)
- 完整 confusables 同形字表、HTML entity/`\u` 转义解码(v1.1)
- 全量"声明域名 vs 提取域名"对账(v1.1 或砍掉)
- 签名/digest/评分(5.13,P5,绑定 12.0)
- 规则矩阵的 per-org 配置化(矩阵是平台行为;org 只调 block 阈值切线)
- `plugin:scan:ack` 权限位(沿用 finding 现有权限模型,细化推迟 10.2 RBAC)

## Decisions

### D1. v1 = 纯规则引擎

regex + 启发式 + 字符集检测,确定性、可审计、零外部成本。LLM 二遍推迟 v2(DeerFlow 两阶段同构)。理由:fail-closed 语义要求可解释的裁决依据;规则命中可以精确指给管理员看。

### D2. 模块落位:新 `plugin/content_scanner.py`,协议不动

`ScanStage`/`ScanResult` 协议留在 `agent_plugins.py`(5.5c 的波动对冲设计);实现类在新模块,service 层接线时用真实实现替换 no-op 实例化。`agent_plugins.py` 零改动或仅极小改动。

### D3. finding schema 前向兼容 + 证据脱敏

```
finding = {
  "rule_id": str,          # 如 "INJ-override", "UNI-tag-run", "SEC-privkey"
  "category": str,         # injection | unicode | secret | tools-audit | url | oversize
  "severity": "high|medium|low",
  "file": str,             # 包根相对路径
  "line": int | None,      # 原文件坐标(尽力;变换后检测可能无法精确定位)
  "transform": "none|nfkc|base64|hex",   # ★ v1 即入 schema,v1.1 混淆层扩展复用
  "evidence": str,         # 截断指纹:前 8 字符 + 长度;绝不存完整秘密/payload
}
```

`transform` 字段是 v1 的前向兼容义务:评审者据此知道"为什么原文肉眼看不出该模式"。证据脱敏遵循 `input_security.py` 审计先例(只发类型计数不发原值)——Snyk 发现 283 个 skill 泄漏 key,`scan_result` 是 JSON 列,明文存证据等于二次落库。

### D4. 定级 = 规则固有严重度 × 文件角色矩阵

定级依据是内容到达 LLM 上下文的路径与频率(已验证 `SkillLoader._format_single_skill` 把 `description + instructions` 拼入 `<skill>` 块注入上下文):

| 文件角色 | 暴露路径 | 注入 | 秘密 | Unicode | 超cap |
|---------|---------|------|------|---------|-------|
| frontmatter description | 技能列表常驻 | high | high | high | high |
| SKILL.md body | 触发时全文注入 | high | warn | high | high |
| mcp.json env/headers/args | 凭证/子进程面 | medium | block | — | — |
| 嵌套支撑文件 | agent 运行时 READ | medium | medium | medium | high |
| README 等人读文本 | 低频偶然读取 | low | medium | low | medium |
| plugin.json 描述 | 目录展示 | low | low | medium | — |

verdict 计算:findings 最高 severity ≥ `AGENT_PLUGIN_SCAN_BLOCK_AT`(env,默认 high)→ block;否则存在 ≥ medium → warn;否则 allow。矩阵是代码常量;org 只调切线。FortiCNAPP 排除 README 的先例在此转化为"README 角色降档"而非"不扫"。

### D5. 混淆解码层:NFKC + 有界 base64/hex 进 v1

- NFKC 归一化作为扫描前处理(全角/兼容形变体现形,标准库零成本)
- base64/hex 候选块须过严格预检(最小长度、字符集、解码后以可打印文本为主)才解码重扫,且只重扫高危规则集——ClawHavoc 的实际投递手法就是 base64 `curl|bash`,此层是针对性防御
- confusables.txt 全表、HTML entity、`\u` 转义解码推 v1.1:增量收益/成本比最差,且调优需要 v1 上线后的 false-positive 反馈数据

已知工程坑:归一化/解码后文本上 regex 报出的坐标是变换后坐标——`line` 字段尽力映射,无法精确时置 null,靠 `transform` + `evidence` 指引人工定位。

### D6. fail-closed 执行语义

- verdict=block → 拒绝安装(行和目录都不落),findings 随错误返回
- scanner 自身异常 → 拒绝安装(绝不 fail-open)
- 文本文件超单文件扫描上限(默认 1MB)→ 产 oversize finding(严重度按角色矩阵),内容不扫但**绝不静默跳过**——22MB 填充攻击的针对性防线;嵌套文件与入口文件定 high(默认阈值下即 block),README 角色定 medium
- 二进制文件按 magic-byte/扩展名跳过,不产 finding(图片字体是正常资产;二进制注入需 agent 后续解析才生效,记为 v1 已知局限)
- ReDoS 卫生:所有模式必须有界量词,禁嵌套不确定量词;单文件扫描设时间预算,超时按 scanner 异常处理(拒装)

### D7. enable 重扫 + 幂等投影 + 存量回填

enable 时,`scan_result` 为 null 或 `scanner_version` 不等于当前 → 重扫。重扫 block → 拒绝 enable。5.5c 时期安装的包(null scan_result)在升级后第一次 enable 自然回填——无需专门迁移任务,一个机制覆盖"新装校验、规则演进、存量回填"三种场景。投影去重键 `(content_hash, scanner_version)`:同键重扫不新增 SecurityFinding 行;`scanner_version` 变化才投影新批次。

### D8. ack 抑制:(content_hash, rule_id) 键控

warn 及以下 finding 被管理员 ack 后,同 content_hash + 同 rule_id 的后续重扫命中降级为 suppressed,不再抬升 verdict;内容变一个字节 → hash 变 → 全部 ack 作废(pin-by-hash 同构,不存在"确认过就永远放过")。≥ block 阈值的 finding 不可 ack 抑制(Hermes "dangerous 不可 --force" 先例)。实现复用 `SecurityFindingService.set_feedback` 机制扩展 acknowledged 态;权限沿用 finding 现有模型,`plugin:scan:ack` 细化权限位推迟 10.2。

### D9. Ops Center 投影走 SecurityFindingModel

finding 行映射:`rule_name=rule_id`、`severity` 直映、`message=category+file+截断证据`、`source_event={phase: install|enable|install-blocked, scanner_version, plugin, origin}`、`metadata_` 存补充字段。被 block 的安装尝试也投影(`phase: install-blocked`,按 plugin name+origin+rule_id 幂等)——合规中心最高价值信号。false_positive 反馈按 rule_id 聚合沉淀为 v2 规则调优数据源,v1 只存不建流程。

### D10. allowed-tools 审计 = 预授权风险面报告

Claude Code 的 `allowed-tools` 实为预授权授予且有未修复绕过 bug(Reversec "Skill Issues")——审计语义定为:声明了 shell 执行/无限制文件写/网络类工具的预授权 → 产 finding 列出授权面(严重度按授予工具危险度),而非仅校验语法合法。

### D11. URL 检测收窄到高置信子集

v1 只检:paste-site 域名清单(ClawHavoc 投递渠道)、IP 字面量端点、同形字混淆域名(punycode confusable)。Dify 式全量"提取 vs 声明"对账误报不可控(文档引 github.com/RFC 链接全成"未声明外联"),推 v1.1。

### D12. 秘密检测:内置规则集,可选联动 detect-secrets

v1 内置高置信规则(私钥 PEM 块、AKIA/ghp/sk- 等 token 前缀、JWT 结构、连接串);部署装有 `[security]` extra 时可选用既有 `SecretsRecognizer` 包装的 detect-secrets 增强覆盖,但**不作为依赖前提**——扫描器在最小安装上必须完整可用。5.5c 已有的 `_HEADER_SECRET_MARKERS` 头内凭证拒绝保留原位,scanner 的 secret 规则覆盖 env/args/正文等更广面。

### D13. go-live:总开关翻转

`AGENT_PLUGINS_INGESTION_ENABLED` 默认值 false → true。翻转后 SaaS 行为不变(stdio 已 default-deny,扫描是叠加防线);自托管获得第三方插件安装能力。开关继续兼作应急 kill-switch。前端零改动(管理页 type 过滤已支持,scan_result 已在 read schema)。

## Risks / Trade-offs

- [规则误报阻塞合法安装] → 角色分层降误报(README 降档)+ ack 抑制消告警疲劳 + 测试基线:合法语料 0 FP(Snyk 0% FP 指标为先例)
- [ReDoS / 扫描 DoS] → 有界模式 + 单文件时间预算 + 超时按异常处理(拒装);单文件 1MB cap 限制单文件扫描成本
- [规则集需要持续演进] → `scanner_version` 进 scan_result;enable 重扫自动应用新规则;ack 按 hash 键控防旧确认误伤新内容
- [新混淆手法绕过(v1 无 confusables 全表)] → `transform` 字段已预留扩展位;v1.1 计划显式登记;ClawHavoc 级 base64 混淆已在 v1 覆盖
- [开关翻转暴露存量] → 存量包 scan_result 为 null,首次 enable 重扫覆盖;无静默暴露窗口
- [二进制内容后续被 agent 解析(如 PDF)注入] → v1 已知局限,记录于文档;后续可按需扩展解析器(不进本 change)

## Migration Plan

1. 无数据库迁移(`scan_result`/`SecurityFindingModel` 均已存在;ack 用 finding 行状态,无新列——如需 `acknowledged` 态字段则 `metadata_` JSON 承载,避免 DDL)
2. 部署即生效:新装走真实扫描;存量包首次 enable 回填
3. 回滚:配置回退(`AGENT_PLUGINS_INGESTION_ENABLED=false`)即回到 5.5c 行为;已写入的 scan_result/finding 行无害留存

## Open Questions

- paste-site 域名清单与注入模式语料的初始集合在实现期从公开研究(Snyk/FortiCNAP/Embrace the Red/CSA)编译,清单维护策略(静态内置 vs 后续可配置)实现期定
- 秘密规则集与 detect-secrets 的联动边界(哪些实体类交给可选库)实现期对齐
