# Tasks

## 1. Runtime 接缝与单步组件

- [x] 1.1 新增 `packages/hecate-runtime/src/hecate_runtime/accepted_decision.py`：`AcceptedAction`/`AcceptedDecisionRecord` 冻结数据类（字段按 design D2：`RunRef`、`schema_version`、`decision_step_id`、`input_refs`、`accepted_output`、`actions` 含实际参数、`to_dict/from_dict`）与 `AcceptedDecisionStore` ABC（`lookup`/`commit(ownership)`），错误词汇 `DecisionConflictError`/`DecisionStoreUnavailableError`/`StaleDecisionFenceError`；docstring 英文，命名避开 `DecisionSink` 词族。验证：模块可导入、`to_dict/from_dict` 往返一致（单测）。
- [x] 1.2 新增 `packages/hecate-runtime/src/hecate_runtime/decision_step.py`：`DecisionResponder` 小 ABC 与 `DecisionStep` 组件（先查后问流程按 design D4；脚本化替身可注入；提交成功才返回 `AcceptedDecisionOutcome`；lookup/commit 存储异常显式上抛不重问模型）。验证：组件单测覆盖先查后问、恢复零模型调用、提交失败不返回已接受、存储不可用≠无决策（断言 responder 调用次数）。
- [x] 1.3 Runtime 包测试：`packages/hecate-runtime/tests/test_decision_step.py` 用 InMemory store（本地测试替身）覆盖 1.2 全部行为分支。验证：全部通过；`grep -n "hecate_durable\|hecate\." packages/hecate-runtime/src/hecate_runtime/{accepted_decision,decision_step}.py` 无跨包导入（自足探针同口径）。

## 2. durable 持久化与双实现

- [x] 2.1 `packages/hecate-durable/src/hecate_durable/storage/models.py`：新增 `durable_accepted_decision` 表（列含部署作用域 ref 对、`decision_step_id`、input_refs/accepted_output/actions JSON、内容摘要、fencing 列、时间戳；唯一约束 `(issuer_domain, run_id, decision_step_id)`；可移植列类型）。验证：SQLite 与 PG 方言均可建表（既有 conftest 双方言路径）。
- [x] 2.2 `packages/hecate-durable/src/hecate_durable/runtime_hook.py`：SQL `AcceptedDecisionStore` 实现——单事务 insert + outbox 事件追加；幂等（同键同摘要返回原记录）/冲突（同键异摘要 `DecisionConflictError` 携带原记录标识）/fencing（复用 lease token 校验，过期拒绝）/存储不可用显式上抛。验证：单元级用例全过。
- [x] 2.3 InMemory 双实现：决策 ABC 在 runtime 侧（非 durable seams），故双实现与 SQL 版同放 `packages/hecate-durable/src/hecate_durable/runtime_hook.py`（`stub.py`/`stub_durable.py` 保持 core 无 runtime 依赖——design D1 的实施修正，已回写）；语义与 SQL 版一致（幂等/冲突/fencing/不可用注入）。验证：与 2.2 共用契约用例（见 2.4）。
- [x] 2.4 参数化契约套件：`packages/hecate-durable/tests/test_accepted_decision_store.py` 以同一组用例（原子提交、幂等、冲突、fencing 拒绝、存储不可用）参数化 SQL + InMemory。验证：两组全过，报告实际用例数。
- [x] 2.5 alembic 迁移：新增 revision 镜像决策表（照 `a2b3c4d5e6f7` 先例，expand-only，拒绝破坏性 downgrade）。验证：在隔离 PG schema 验证该 revision 的 upgrade、metadata 一致性与拒绝破坏性 downgrade（保留表和事实）；迁移产物列与包 metadata 一致（断言测试或脚本核对）。

## 3. PG 并发与重启恢复

- [x] 3.1 PG 用例（`packages/hecate-durable/tests/` 或 `tests/test_execution/`，接 `HECATE_STEP6_POSTGRES_URL` 既有逐测试 schema harness）：双连接并发提交同键（至多一个接受，另一方幂等或显式冲突）；过期 fencing token 提交被拒；真实独立 Python 子进程间恢复找回原决策且内容逐字一致。验证：未配置 URL 时显式 skip 并注明理由；配置后全过。
- [x] 3.2 组件级 PG 重启：`DecisionStep` + SQL store 在真实 PG 上验证"提交进程退出后另一独立进程 lookup 返回原记录、responder 零调用"。验证：同 3.1 的 skip 语义；verification.md 单列实际 PG 运行结果。

## 4. 收尾验证

- [x] 4.1 显式收集运行：`python -m pytest packages/hecate-runtime/tests packages/hecate-durable/tests -q` + 上述新增测试文件 + `tests/test_runtime/test_runtime_self_sufficiency.py tests/test_layering_domain.py`。验证：全过并报告实际收集数；自足探针确认 Runtime 新模块无跨包导入。
- [x] 4.2 共享检查 `bash scripts/dev.sh check all`。验证：0 错误。
- [x] 4.3 汇总 `verification.md`：各套件实际通过/跳过数、PG 是否实际运行（未运行则如实标注并说明哪验收未闭合）、既有 flake 不误报。验证：PR 描述含实际结果；边界声明（非完整 Controller）保留。

## 5. 复核修正

- [x] 5.1 复用真实 LeaseManager，在同一提交事务内校验 Run 作用域、holder、精确 token 与期限；重复提交同样校验，不以决策表充当租约真源。
- [x] 5.2 实现决策与 outbox 同事务、严格 JSON/版本校验、内容散列核验、输入版本冲突及快照隔离；补齐故障与并发回归。
- [x] 5.3 使用隔离 PG schema 与真实子进程验证恢复；验证 expand-only 迁移和元数据一致性，回退保留事实。
- [x] 5.4 更新规范、设计与实际验证记录，执行相关套件、分层/自足探针和共享检查。
