# Proposal

## Why

演进方案 step6 仅剩一条未勾选(`docs/refactor/enterprise-agent-platform-evolution-plan.md:525`):审计留存的容量阈值、留存策略与存储失败显式行为,以及审计留存的委托面。该条的其余子句已在此前 change 中交付——写动作意图先于执行并落回执(Action 台账)、关键证据不可写时停止新受保护动作(evidence probe fail-closed)、执行后落盘失败保持待对账(`reconciliation_required`)、中心离线不影响本地事实留存(SC05)。

当前宿主证据存储(`hecate_runner/evidence.py`)是按天滚动的 JSONL 文件:无容量上限、无留存/过期策略、存储失败仅有"不可写即拒绝"一种显式行为,且审计留存没有可委托的第二实现。本 change 收掉 step6 这最后一条,不重做已交付子句。

## What Changes

- **留存策略面**:`runner.json` 新增可选 `evidence` 配置块(`retention_days`、容量上限、后端选择);未配置时行为显式声明(不设上限、不过期,并在能力摘要中如实报告)。
- **容量与过期的显式行为**:JSONL 后端按文件年龄执行留存清理(删除超过 `retention_days` 的天文件),按总字节数执行容量检查;留存清理后仍超容量时,证据门对新受保护动作 fail-closed(沿用 SC06 本地半边语义),readonly 动作不受影响;门拒绝的原因可查询。
- **存储失败行为分类显式化**:证据写入失败按"不可写/超容量"分类记录(`evidence_gate` 拒绝记录),健康端点报告证据后端、策略与当前用量。
- **委托面(第二实现)**:`evidence.backend: "durable"` —— durable profile 宿主可把审计留存委托给已验证的本地 SQL 存储(与 durable 任务库同库、同引擎),按行数计容量、按年龄执行过期;JSONL 保持默认。两个后端同契约(append/query/probe + 策略执行),参数化测试套件钉住一致语义。不新增无第二实现的运行时扩展点。

非目标:中心上传缓冲与上传重试语义(step10)、平台侧审计表变更(平台 `audit_logs` 不动)、SC06 翻转(中心半边归 step10,manifest 保持 `planned`)、审批语义(step7)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `standalone-runner-host`: 现有 "Local evidence with no default upload" 语义保持,新增证据留存策略需求(容量阈值/留存期/存储失败显式行为/本地 SQL 委托后端)。

## Impact

- `packages/hecate-runner/src/hecate_runner/evidence.py`:策略化改造(容量/过期/失败分类),保持 append/query/probe 契约。
- `packages/hecate-runner/src/hecate_runner/profile.py`:`evidence` 配置块解析与校验;能力摘要补证据后端/策略。
- `packages/hecate-runner/src/hecate_runner/server.py`、`engine.py`:门拒绝原因分类、健康端点摘要。
- 新增 SQL 证据后端(durable 库同引擎新表,宿主本地库,不触平台表);alembic 不涉及(宿主库经 `create_schema` 自建)。
- 测试:`packages/hecate-runner/tests/` 策略与双后端参数化用例;演进方案 step6 末项勾选。
- 文档:`packages/hecate-runner/README.md` 证据留存段。
