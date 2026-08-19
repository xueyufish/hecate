# Tasks: plugin-content-scanning (5.13a)

## 1. 扫描器核心（`plugin/content_scanner.py`，新模块）

- [x] 1.1 定义 finding 数据类（rule_id/category/severity/file/line/transform/evidence）与证据截断辅助（前 8 字符 + 长度）
- [x] 1.2 注入规则集：指令覆盖话术、伪 system-prompt/tool-result 框架、外传命令（语料编译自 Snyk/FortiCNAPP/Embrace the Red/CSA 公开研究，见 design 开放问题）
- [x] 1.3 隐形 Unicode 规则集：零宽（U+200B-200D/2060/FEFF）、bidi（U+202A-202E/2066-2069）、变体选择符、ANSI 转义、tag 字符块；阈值：连续 run >10 codepoints = high，单文件可疑 codepoint 总数 >100 即报
- [x] 1.4 秘密规则集：PEM 私钥块、AKIA/ghp/sk- 等 token 前缀、JWT 结构、连接串（detect-secrets 可选联动的边界实现期对齐，不作为依赖前提）
- [x] 1.5 allowed-tools 预授权审计（shell/无限制文件写/网络类授予 → finding 列授权面）+ URL 检测（paste-site 域名清单、IP 字面量、同形字混淆域名）
- [x] 1.6 混淆解码层：NFKC 归一化前处理；base64/hex 候选块严格预检（最小长度/字符集/解码后以可打印文本为主）后解码重扫高危规则集；finding 记录 transform
- [x] 1.7 文件角色枚举 + 角色×规则定级矩阵（design D4 表）+ verdict 计算（severity 对阈值切线，allow/warn/block）
- [x] 1.8 文件遍历与预算：文本/二进制识别（二进制跳过无 finding）、单文件 1MB cap 产 oversize finding（严重度按角色）、全模式有界量词 + 单文件时间预算（超时按异常处理）
- [x] 1.9 `RuleEngineScanStage` 实现 `ScanStage` 协议（`agent_plugins.py` 协议不动）+ `scanner_version` 版本常量

## 2. 管线接线（`services/plugin/service.py`）

- [x] 2.1 install 路径以真实实现替换 no-op：block 裁决拒装（行/目录不落，findings 随错误返回）、scanner 异常拒装（fail-closed）
- [x] 2.2 install 成功路径持久化 `scan_result`（verdict/findings/scanner_version）
- [x] 2.3 enable 重扫：`scan_result` 为 null 或 `scanner_version` 漂移时触发；重扫 block 拒绝 enable；存量包首次 enable 回填

## 3. 投影与 ack

- [x] 3.1 SecurityFindingModel 投影：install/enable/install-blocked 三相；按 `(content_hash, scanner_version)` 幂等去重，blocked 尝试按 (name, origin, rule_id) 幂等
- [x] 3.2 ack 抑制：扩展 `SecurityFindingService` acknowledged 态（`metadata_` 承载，无 DDL）；重扫前按 `(content_hash, rule_id)` 查询已 ack 命中并降级；≥ 阈值 finding 不可抑制；内容变更（hash 变）ack 自然失效

## 4. API 与配置

- [x] 4.1 `GET /api/plugins/{id}/scan` 端点（verdict/findings/scanner_version/时间戳；非 agent-plugin 返回 not applicable）
- [x] 4.2 配置项：`AGENT_PLUGIN_SCAN_BLOCK_AT`（默认 high）、`AGENT_PLUGIN_SCAN_FILE_CAP_MB`（默认 1）等；`AGENT_PLUGINS_INGESTION_ENABLED` 默认值翻转为 true

## 5. 测试（`tests/test_services/test_plugin/` 等，遵循 conftest 惯例）

- [x] 5.1 恶意样本 golden 测试：注入话术/Unicode 走私（tag run >10）/秘密/base64 混淆外传/超 cap 文本，按角色矩阵断言档位
- [x] 5.2 合法语料 0-FP 基线：正常 SKILL.md/README（含祈使句、文档链接、零星零宽字符）不产 finding
- [x] 5.3 verdict 三档 + 阈值切换（high/medium）测试；fail-closed 测试（scanner 异常拒装、超时拒装）
- [x] 5.4 enable 重扫触发条件（null/版本漂移）、重扫 block 拒绝 enable、存量回填、ack 抑制与 content 变更失效测试
- [x] 5.5 投影幂等（同 (hash, version) 不重复建行）+ blocked 尝试记录 + `GET /{id}/scan` 端点测试

## 6. 验证与文档

- [x] 6.1 全量验证：`ruff check` + `ruff format --check` + `mypy` + `pytest tests/ -q` 全绿
- [x] 6.2 更新 `docs/features/feature-catalog.md` 与 `roadmap.md` 5.13a 条目：补入 Google GE Governing Agent Skills、DeerFlow SkillScan、Hermes-agent、FortiCNAPP SK-* 参照；记入 openJiuwen 与 AgentArts 同源事实
