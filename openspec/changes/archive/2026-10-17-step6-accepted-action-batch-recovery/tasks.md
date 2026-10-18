# Tasks

## 1. 批处理组件（runtime）

- [x] 1.1 新增 `packages/hecate-runtime/src/hecate_runtime/accepted_action_batch.py`：`AcceptedActionBatch`（或模块函数）——入口 `(run_ref, decision_step_id, ownership/context)`，从记录派生顺序逐个调用 `AcceptedActionStep.dispatch`，`failed/withheld/stopped` 即停；返回逐动作 `AcceptedActionOutcome` 列表 + `unprocessed_action_keys`；docstring 英文、明示"仅代表这些动作的结局、不推进 Task"。验证：模块 mypy/ruff 通过。
- [x] 1.2 如需复用，从 `accepted_action_step.py` 提炼公共校验/动作定位为模块级函数（原 `dispatch` 既有调用语义保持兼容；可选摘要约束供批处理使用）。验证：既有单动作测试零改动全绿。
- [x] 1.3 runtime 单测：正常全过、A2 failed/withheld/stopped 三种终态即停、未处理键正确、调用方注入动作列表被忽略、空/缺失记录错误透传。验证：`packages/hecate-runtime/tests/` 新增文件全绿。

## 2. durable 集成测试（真账本、真进程）

- [x] 2.1 SQLite 集成：A1 落盘后重入批处理 → A1 回填真实结果（底层工具恰一次）→ A2 执行；A2 未知/停止 → A3 零派发且二次恢复不重做 A2。验证：`packages/hecate-durable/tests/` 新增用例全绿。
- [x] 2.2 PostgreSQL 集成：同场景 + `accepted_action_process.py` 真双进程——进程一 A1 后退出、进程二恢复续 A2；双进程并发批处理同一写动作 → 首次执行恰一次。验证：PG 标记的用例在本地 PG 可达时通过（跳过条件与既有 PG 测试一致）。
- [x] 2.3 租约/接管：A1 后 ownership 过期或换主 → A2 派发被拒进入停止终态、A3 零派发。验证：集成用例全绿。

## 3. 规范与文档同步

- [x] 3.1 delta spec 与实现核对：终态分类逐一对应 `AcceptedActionOutcome.status` 实际取值；"首次执行恰一次"断言与账本 fencing 语义一致。验证：`openspec validate` 通过。

## 4. 回归与验证

- [x] 4.1 既有回归显式收集：runtime 既有 accepted-action 单测、durable bridge/PG/store 套件、runtime 自足探针与分层。验证：全部通过，既有断言零放宽。
- [x] 4.2 共享检查 `bash scripts/dev.sh check all`。验证：0 错误。
- [x] 4.3 汇总实际结果到 `verification.md`（命令输出、收集数、PG 实跑情况、跨进程测试证明、边界与保留限制）。验证：tasks 全勾；PR 描述含实际结果。


## 5. 复核修正

- [x] 5.1 固定并逐动作核验决策摘要，拒绝不同存储/中途返回的替换决策；非法 step 身份在存储访问前拒绝。新增回归先复现后修复。
- [x] 5.2 区分停止原因并保留前缀：内容冲突、存储故障/损坏/记录消失、owner 失效、参数拒绝；取消和审批等待信号透传。
- [x] 5.3 真独立进程在首次写动作回执提交前重叠竞争，第三进程回填全部结果；合法新 owner 恢复已完成前缀。SQLite/PostgreSQL 双库实跑。
- [x] 5.4 完整 runtime/durable 与旧恢复/自足/分层回归，共享检查和 strict OpenSpec 校验；保存最终结果到 verification.md/review.md。
