# Verification

> 本文保留 GLM 初验记录。后续 Codex 复核发现并修复快照合并、旧错误回执回填和验收证据缺口，最终结论以 [review.md](review.md) 为准。下文 CI 覆盖、MCP/Workflow 完成范围及跨平台结论不作为最终认证。

## Scope

基线 `22dbd504`。四组交付 + 用户确认的范围扩展(选项 C:断连恢复链三处产品修复)。代码面:`hecate-runtime` `action_ledger.py`(succeeded-无内容分流)、`workers/tool_worker.py`(`reconciliation_required` 升级停止 + `_log_recorded_content` 日志回填)、`studio/workflows/execution_service.py`(断连保存 shield + 合并持久化);测试面:真实 TCP 断连 harness、MCP 多轮、workflow 子任务真引擎用例、G2/契约单测语义更新;文档面:G3 决策记录增补、演进方案 G3 项增量登记与归档链接修正。

## Results

### 0. 断连恢复链产品修复(范围扩展)

- **shield**:断连 checkpoint 保存以 `CancelScope(shield=True)` 保护——诊断证据:无 shield 时取消展开在第一个 await 再抛,快照静默丢失(实验复现)。
- **合并持久化**:agent_state 快照合并保留引擎 channel_state 与 metadata——诊断证据:整对象覆盖后快照 channel_state 为空、恢复重放整轮(实验复现)。
- **日志回填**:恢复决策新增第三回填权威(会话日志 messages 通道 CHANNEL_WRITE,携带完整工具结果消息)——诊断证据:此前跨轮续接把 `[reconciliation required]` 占位文本喂给模型(既有 G3 多轮测试注释记载的边界),现回填真实内容。

### 1. 回执恢复行为

- succeeded-无内容分流:只读→安全重执行;写类→`ExecutionStoppedError("reconciliation_required")`,占位文本不再进入模型消息。
- 单测:`test_g2_recovery_states.py` 改写两用例 + 并发重复派发方显式停止;`test_event_store_contract.py` 同步;`[conflict]`/`[needs review]` 语义回归不变。

### 2. 真实 TCP 中途断连与恢复

- harness:in-thread uvicorn 真实 socket;原始 socket 客户端 SO_LINGER=0 RST 中途断连;三引擎隔离(请求 NullPool/注册专池/读侧)+ WAL——均经诊断驱动(优雅关闭不触发服务端取消、请求连接撕裂毒化池、双连接锁,逐一实验定位并修复)。
- 用例 A:cancelled 终态 → 恢复零重复 + 真实内容回填 + 200 答案,通过。
- 用例 B(注入:回执在、messages 通道写被滤):`reconciliation_required` 停止、零二次执行、响应不含占位文本外的内部细节,通过。
- SQLite 层默认运行(2 passed);PG 参数化沿用 `HECATE_STEP6_POSTGRES_URL`。

### 3. MCP 入口

- 新增多轮续接用例:第二轮真实回执回填(非占位)、成功工具零重复;既有单轮事件配对/关联保持(2 passed)。

### 4. Workflow 子任务入口

- 真引擎工具环用例经真实 task-control 链:工具恰好一次、配对引擎事件、真实结果进入最终答案、父任务经核实回调收敛、唤醒零重复。**仅 PostgreSQL 层运行通过**(本地容器 1 passed,3m48s):task-control 内联派发 + 工具动作账本在 SQLite 构成双同步连接单写者争用(`database is locked`,实验复现),属存储层限制;未配置 URL 显式 skip(理由注明,不以 skip 冒充通过)。CI PG job 已含该模块路径。

### 全量验证

- 宽回归(channel/execution/A2A/scheduling/workflow/MCP agent_chat/IM/routing/分层):首轮 836 passed、5 failed——失败全部为 `tests/test_services/test_workflow` 的 AsyncMock 存储桩违反 `load` 契约(返回 Mock 而非 `SessionState | None`),按契约修正桩后该目录 67 passed;最终复跑:**841 passed, 40 skipped, 0 failed**(234s)。
- `tests/test_services/test_workflow`:67 passed(桩契约修正后)。
- runtime 全目录(含改动单测):1476 passed;受影响入口定向:1603 passed。
- `ruff check`/`format --check`:通过;`mypy src/`:635 files 零错误;`openspec validate --strict`:通过。

## 边界与未验证项(如实记录)

- 默认开关维持关闭;MCP 拒绝场景显式证据、放量记录、真实模型轨(step5e)为翻转前置剩余缺口——已登记入 G3 决策记录与演进方案 G3 剩余项(该项维持未勾选)。
- 真实 TCP 断连证据在 Windows 本地 + Linux CI 的 RST 语义下成立;ASGI 既有用例不再单独宣称断连证据。
- workflow 子任务用例的 SQLite 锁限制已登记;独立 workflow 的 Run/部署身份另立设计。
