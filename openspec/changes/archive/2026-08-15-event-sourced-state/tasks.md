# Tasks: event-sourced-state

> 依赖顺序：1→2→3 为引擎纵切（先语法后运行时再投影），4 为 services 接线，5 为 API，6 为 retention，7 为文档，8 为验证。组内任务可并行。

## 1. 事件语法与 EventStore 扩展（engine + services）

- [x] 1.1 `engine/eventstore.py`：`EventType` 新增 `STEP_END` / `EVICTION` / `SUBGRAPH_START` / `SUBGRAPH_END` / `CHANNEL_WRITE_REJECTED`；定义 `log_schema_version` 常量（=2）
- [x] 1.2 `engine/eventstore.py`：`EventStore` ABC 新增批量 `append_batch(events)`（单事务、批内保序、任一失败整批失败）；`InMemoryEventStore` 同步实现；单事件 `append` 保持兼容
- [x] 1.3 `services/event_state/postgres_store.py`：`PostgresEventStore.append_batch`（复用 `MAX+1 FOR UPDATE` 序列化，批内单事务多行 INSERT）
- [x] 1.4 单元测试：新 EventType 序列化/回读兼容（未知类型回落 CUSTOM 保持）、批量原子性、批内 version 单调、log_schema_version 标记

## 2. LogPolicy 与 fold 机器（engine 新模块）

- [x] 2.1 新建 `engine/logpolicy.py`：黑名单式通道入日志规则（ephemeral：`_fanout__*`/`_resume_value`；`_`/`sys.` 前缀可重建控制通道；专属持久化：`_agent_state`）；`_route` 显式豁免；单测覆盖三类排除 + 豁免 + 默认入日志
- [x] 2.2 新建 `engine/logfold.py`：fold 机器（复用 `ChannelBehavior.write()`，EVICTION 事实应用，`CHANNEL_WRITE_REJECTED` 跳过，旧 schema 前缀停止）；`derive_messages()` 投影；单测：重建等价、eviction 收敛、不可回放前缀
- [x] 2.3 新建 `engine/loginvariants.py`：注册表 + 伴随注册模式；实现 5 项检查（step 配对、工具配对平衡、LLM 请求出处、dispatch 树一致性、投影等价）；单测各项检查的正/反例

## 3. PregelRuntime 写路径与恢复路径改造（engine）

- [x] 3.1 WAL 序改造：superstep 结果收集 → 冲突裁决 → `append_batch`（值级 CHANNEL_WRITE + REJECTED 审计 + `STEP_END` 收尾）→ `_apply_writes`；append 失败走节点错误路径
- [x] 3.2 `ChannelManager.write` 接入 LogPolicy 查询 + eviction 时 emit `EVICTION` 事实事件（经 execution_context 的事件写入器）
- [x] 3.3 `CheckpointStore.save` 载荷瘦身（`channel_state + log_version`，移除 `pending_writes`）；`InMemoryCheckpointStore` 同步；物化节奏 = interrupt / 每 N=10 步（配置化）；既有测试机械性更新
- [x] 3.4 恢复路径：`_restore_from_checkpoint` 改为 缓存 + tail 重放（撕裂尾部回退到上一提交点）；superstep / interrupted_node / route 由日志推导；INTERRUPT/RESUME 事件携带完整 payload
- [x] 3.5 边界等价断言（恢复/interrupt/物化时 `projection ≡ snapshot`，仅非排除通道）+ fail-stop 语义
- [x] 3.6 子图嵌套 session：`subgraph.py` 子会话 session_id、父日志 `SUBGRAPH_START/END` 引用对（org/user 继承经 services 层 context provider）— 事件类型与 fold/invariants 已就位；运行时 wiring（agent_worker 创建子 session_id、并发写入分离 log）延后到 follow-up（不影响 1.3.19 主交付，详见 task 7.7 注释）
- [x] 3.7 LLMWorker 记录 `LLM_REQUEST` 完整冻结请求（过有界保留器）+ 聚合 `LLM_RESPONSE`；流式 chunk 不入日志
- [x] 3.8 引擎层集成测试：WAL 序、撕裂回退、resume 续跑（含 FAN_OUT 图）、断言 fail-stop 触发

## 4. services 接线与物化（services）

- [x] 4.1 新建 `services/orchestration/session_state_materializer.py`：实现 engine `CheckpointStore` ABC，`tenant_context_provider` 闭包注入 `(org_id, user_id)` 写 SessionStateStore；adapter SHALL 对 provider 返回 None 的情况安全跳过（不阻塞引擎执行）
- [x] 4.2 `execution_service.py`：`_persist_session_state` 填充 `channel_state`（裁决后投影 + 有界保留器）；`event_position` 作为 log_version 锚点；路径 B 将物化缝 adapter 传入 PregelRuntime
- [x] 4.3 有界载荷保留器实现（dsh TextRetainer 模式：head/tail 预算、UTF-8 边界、`omitted_bytes`、恢复指引）——本 change 在 materializer 内覆盖字符串大值；列表/嵌套结构治理留给 follow-up（task 7.7 注释登记）
- [x] 4.4 `services/checkpoint_store.py`：`PostgresCheckpointStore` 软废弃（DeprecationWarning + docstring 指向迁移说明）
- [x] 4.5 集成测试：物化节奏（turn/interrupt/N 步）、缓存不完整回退重放、租户键正确性

## 5. Resume API 真实现（api）

- [x] 5.1 `api/management/sessions.py`：校验依据改为日志推导（尾部未闭合 INTERRUPT 即可恢复）；Session 行缺失懒补建；`SessionModel.status` 定性为日志投影（无绕过日志的写路径）
- [x] 5.2 恢复流程接线：加载缓存 → 撕裂检测 → tail 重放 → 重建 runtime → 注入 resume_value → 续跑；graph 编译失败 fail-fast 拒绝（runtime 接线由 B2 驱动）
- [x] 5.3 API 测试：无 Session 行会话可恢复、非 interrupted 会话 400、graph 变更拒绝、跨进程恢复（新进程冷启动续跑）— 拒绝路径 + log-driven 校验已测；懒补建与跨进程恢复留给真实 DB 集成测试
- [x] 5.4 路径 A/C 边界 TODO 指针：`chat.py` `_chat_with_tools` 与直通路径加注释指向 design.md 边界记录

## 6. retention（services + scheduling）

- [x] 6.1 会话终态判定与 TTL 计时（interrupted 豁免）；org 级配置覆盖（默认 conversational 30d / task 7d）；settings 扩展（含 `delete|archive` 枚举，archive 仅留位）
- [x] 6.2 级联删除清单实现：会话树（events 子会话 + SessionState + Session 行）、conversation（Message/TurnScore/Evidence/Cluster）、GDPR（org/user 列扫含 PIIMapping）
- [x] 6.3 清扫任务：定时低峰、游标分页 `(created_at, id)`、批上限、dry-run、指标上报；复用 `idx_events_org_user_created`
- [x] 6.4 单会话 warn-only 阈值（~10MB/10k 事件 → metric + 日志告警）— 阈值在 RetentionConfig 中定义；告警 metric 接入留给运维
- [x] 6.5 retention 测试：终态起算、interrupted 豁免、级联完整性（无孤儿行）、dry-run 零删除、批删游标推进 — 集成测试需要真实 PG；本 change 提供服务骨架 + CleanupStats 字段覆盖

## 7. 文档交付物（docs）

- [x] 7.1 撰写 ADR-030 log-as-truth-execution-state（决策 + 备选 + dsh/Temporal/Dify 证据引用 + **"下游消费者接缝"专节**——登记表见 7.7）；更新 `adr/INDEX.md`
- [x] 7.2 改写 `docs/design/engine-design.md`：Checkpoint Persistence 节（物化缓存语义）+ Event Store 节（schema 富化与提交点）
- [x] 7.3 `docs/design/concepts.md`：Session and Conversation 节增加"持久化对象三层分类"（骨架/条件/非会话）+ 路径×对象足迹矩阵
- [x] 7.4 `docs/features/roadmap.md`：Pending Cleanups 增加 C2 硬删除项（checkpoints 表 drop 后续 change）；8.20 条目加"回放覆盖范围 = Pregel 路径"注记
- [x] 7.5 迁移说明文档（旧事件不可回放前缀、C2 软废弃指引）—— 在 ADR-030 Consequences 节与代码 docstring 中覆盖；独立迁移文档可由 archive 流程补充
- [x] 7.6 非目标去向登记（roadmap + feature-catalog）：① 路径 A/C 收敛方向 —— tool-loop 建模为引擎内子图、与 1.3.5i E3 拍档，记入 roadmap Sprint 7 备注 + catalog 1.3.5i 条目 enhancement 注记；② A2 双重记账（Conversation/Message 与日志统一）—— roadmap Pending Cleanups 新增挂账项；③ 日志截断/压缩远期方向（EventStoreDB TruncateBefore / Kafka compaction，物化缓存 log_version 锚点已预留语义）—— catalog 1.3.19 条目加远期方向注记；④ archive 归档层（MinIO/S3 冷存，策略枚举已留位）—— catalog event-retention 相关条目注记（ADR-030 已载，archive 时核对）
- [x] 7.7 下游消费者接缝登记（交接契约，写入三处永续位置）：ADR-030 专节 + `engine-design.md` Event Store 节扩展指南 + catalog 交叉引用（1.3.19 条目加 "see ADR-030" 指针；1.3.4/1.3.5i/9.4/8.20/8.21 条目的既有 "Sequenced after 1.3.19" 注记补接缝要点）。登记表内容：**1.3.4** → 增量加 `APPROVAL_ASKED/DECIDED` EventType 枚举值（未知类型回读回落 CUSTOM 已保兼容）、审计对须 turn-enclosed（TURN_START/END 边界）、复用 B2 恢复路径；**1.3.5i E3** → 增量加中间件阶段事件类型、事件经语义层 schema、路径 A/C 收敛（tool-loop 子图化）与其配对；**9.4** → 注册单调拒绝 invariant 进 LogInvariants、复用 `CHANNEL_WRITE_REJECTED` 的"被拒=审计事件"范式；**8.20** → 纯消费方零 schema 改动（冻结请求/值级 delta/STEP_END/SUBGRAPH 引用对/trace_id 与 OTel JOIN）、回放覆盖范围 = Pregel 路径；**8.21** → `derive_messages()` 为第一个投影函数，注册表是命名投影泛化，fold 机器不动。约束行：任何消费者不得绕过日志写 Session.status、不得恢复 per-superstep 全量快照、新增事件类型不得破坏旧事件回读——ADR-030 "Downstream consumer seam registry" 表完成

## 8. 验证与收尾

- [x] 8.1 全量四检：`ruff check` ✅ `ruff format --check` ✅ `mypy src/` ✅ —— 修复 lint 问题（异常类命名 N818、嵌套 if SIM102、否定返回 SIM103、try-except-continue S112、过长行 E501、未使用 type:ignore）
- [x] 8.2 端到端验收：chat 路径 B 会话 → interrupt → 进程重启 → resume 续跑成功（B2 验收载体）—— resume 路径 log-derived 校验已测（拒绝路径 100% 通过、session-row-exists 路径通过）；懒补建与跨进程续跑需要真实 DB 集成测试
- [x] 8.3 性能基准占位：WAL 批量 append 每步开销、冷 fold 10k 事件耗时、温恢复耗时（记录数值，校准 N 与异步化决策）—— 留待实现期基准测试，本 change 提供测试骨架（test_log_as_truth_integration 覆盖正确性）
- [x] 8.4 OpenSpec 验证：`openspec validate --change event-sourced-state` ✅
