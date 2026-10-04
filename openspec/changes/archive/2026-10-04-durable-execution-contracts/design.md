# Design

## Context

见 [proposal.md](proposal.md) 的 Why:step6 两轨并行需要一个先定稿的契约基线。既有事实(勘码结论):

- `contracts/execution/events.py` 已有 `EventEnvelope`(event_id、task_ref/run_ref、source_sequence、correlation/causation、payload_schema_ref、evidence_ref、gap、extra),缺 `actor`/`source`。
- `execution/backend.py` 已有 `RunState`(pending/running/succeeded/failed/cancelled/unknown,后端观察态)与 `CancelRequestState`(requested/acknowledged/applied/rejected/unknown,缺 expired)。两者 docstring 均注明"plan-level Task states belong to step6"。
- `tool-recovery` spec 已定义 execution_id、arguments_digest、四态恢复、会话级领取锁、摘要冲突——语义完备但锚定在 Runtime EventStore 层。
- 契约层既有执法模式:权威 JSON Schema(`contracts/schemas/`)+ dataclass 映射 + 标准样本三方互检、AST 纯度探针、`StubExecutionBackend` 第二实现、`tests/test_execution/` 参数化契约测试。

## Goals / Non-Goals

Goals:

- 两轨(durable-execution-core / platform-task-control-api)需要的全部共享符号在本 change 一次落地,两轨 PR 不修改 `contracts/` 与接缝文件。
- G2 四态语义获得跨进程契约形态,与 Runtime 层语义由漂移测试钉住。

Non-Goals(设计层面补充):

- 不触碰 `core/composition/` 装配——注入点由两轨各自的 change 接线,本 change 保持零装配改动,进一步压缩冲突面。
- 不定义字段所有权矩阵(平台 vs 宿主谁写哪个字段)——归 `task-run` 既有要求与两轨契约;本契约只固定状态/回执/幂等语义。
- 不设计 HTTP/OpenAPI 绑定——持久执行暂无进程外消费者;schema 即接口,出现真实进程外需求时按 step3 模式补绑定。

## Decisions

**D1 schema 放置与命名**:新增 `contracts/schemas/durable-task-state.schema.json`、`durable-command-record.schema.json`、`durable-action-ledger.schema.json`(含四态与意图/领取/回执对象);`event-envelope.schema.json` 增加可选 `actor`/`source`(加法演进,复用既有 `$id` 与版本空间)。备选是独立 `durable/` 子目录——放弃,现有 schemas 平铺,保持一致。治理 profile 不建独立 schema 文件,以"envelope + 必填断言"实现:映射层提供 `validate_governance_event()` 失败即拒绝,样本覆盖正负例。

**D2 状态枚举落地位置**:`TaskLifecycleState` 新枚举放 `contracts/execution/durable.py`(新映射模块);`CancelRequestState` 在 `execution/backend.py` 原地增补 `EXPIRED`(加法,step3 契约测试回归覆盖)。不把 Task 状态塞进 `backend.py`——两者维度不同(docstring 已预留此分离)。控制命令种类 `ControlCommandKind`(cancel/pause/resume/provide_input)与命令记录 dataclass 放 `durable.py`;`CancelReceipt`/既有取消流保持不动,命令记录契约统一承载回执语义。

**D3 接缝形态**:`execution/durable.py` 新模块,三个 `abc.ABC`:`DurableTaskStore`、`ControlCommandRecorder`、`ActionLedger`,方法出入参全部为契约 dataclass(复用 `BackendRef` 引用)。遵循 runtime-pluggability 规则:第二实现即 `execution/stub_durable.py` 的 InMemory 版本;具名消费者为两轨(worktree A 的 PostgreSQL 实现、worktree B 的平台服务)。备选 Protocol——放弃,ABC 与 `AgentExecutionBackend` 既有形态一致且可承载 docstring 语义。

**D4 四态与 Runtime 层的一致性钉子**:契约四态枚举值与 `tool-recovery` 恢复分支的判定值取相同字符串;漂移测试同时钉住四态字符串、副作用类别五值(与 `contracts/execution/tools.py` 既有钉子并列)。任何一侧单方变更即测试失败——这是"语义对齐"的可执行形态,替代文档性对齐。

**D5 幂等键**:契约 dataclass `IdempotencyKey`(subject、workspace、request_digest、key 本体);摘要为规范化 JSON(canonical serialize + SHA-256),与 `tool-recovery` 的 `arguments_digest` 同一算法族。`DurableTaskStore.record_submission()` 同时登记键与 Task/Run 关联,同键异摘要返回 `IdempotencyConflict`。作用域绑定由调用方注入服务端身份,契约校验"键内 subject/workspace 与上下文不一致即拒绝"。

**D6 样本与测试**:`tests/test_execution/samples/durable/` 下标准样本(状态迁移、命令回执、幂等正负例、治理事件正负例、四态恢复);`test_durable_contract.py` 参数化跑 InMemory Stub;conftest 参数集留注册位,两轨实现合入时把 PG/平台 adapter 加进参数集即自动获得全部断言。纯度探针(AST 扫描)按既有模式把新模块纳入扫描范围。

**D7 provide_input 载荷形态**:命令记录携带可选 `payload` 对象 + `payload_schema_ref`,与 `EventEnvelope` 的载荷模式同构(未知字段容忍并保留);补充输入的具体 payload schema 由消费方(`platform-task-control-api` 及实际交互场景)定义,契约层不预建投机 schema。备选是现在为补充输入定义专用结构——放弃:消费者尚不存在,违反"无具名消费者不建契约"的既有规则,且消费方 later 定义落在加法演进路径上,不影响本契约任何要求。

## Risks / Trade-offs

- [契约先行可能被两轨实况推翻] → 契约保持 0.x 不冻结(与 step3 同策略); Stub 与参数化测试让修订成本集中在样本与断言,单点修改;两轨 design 阶段若发现缺口,以增量 PR 修订本契约而不是各自绕开。
- [`CancelRequestState` 增补 `expired` 影响既有序列化消费方] → 加法枚举值,序列化容忍未知值的既有策略不变;既有取消流测试全量回归。
- [接缝方法集偏小可能不够两轨用] → 宁小勿大:缺口出现时先在消费方 change 内以增量 PR 扩展契约(带样本与测试),不预建投机方法;runtime-pluggability 规则本身即反对预建无消费者接口。
- [治理 profile 不建独立 schema,校验逻辑在映射层] → 换取 envelope 单一权威定义;样本正负例 + 三方互检保证校验不漂移;若未来治理事件需要独立版本空间,再拆 profile schema(加法)。

## Migration Plan

纯加法,无部署与数据迁移。合并顺序:本 change 合入 main 后,两 worktree 从该提交拉出。回滚即 revert 单个 PR,无状态需要恢复。
