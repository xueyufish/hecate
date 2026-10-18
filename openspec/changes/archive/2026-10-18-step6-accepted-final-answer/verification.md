# Verification

## 基点与范围

基点 `799476f9`（#285），分支 `feat/step6-accepted-final-answer`。审查在用户指定 worktree 中进行，未提交、未推送，也未修改其他 worktree。

入场时 `.agents/skills/openspec-propose/SKILL.md` 已存在无关差异。本次保持原样，不将其视为最终回答功能的一部分；建议提交本 change 时排除该文件。

## 审查修正

- 原实现将任意非 null `tool_calls` 块都计为工具意图，导致 `[]` 加有效文本也被误拒。新增的四个空列表回归用例在修正前全部失败；修正后仅非空列表记为已出现工具调用。从未出现非空调用时允许纯文本最终回答，先前非空调用被清空时仍拒绝。
- 非字符串且不可哈希的 proposal kind 原先触发 TypeError；现在统一显式抛 DecisionValidationError，且不提交记录。
- 补齐 v1 固定摘要与 opaque accepted_output 的兼容用例，确认历史 kind/text 字段不被重新解释；修正 v2 携带动作的用例为实际非空元组。
- 将真实 ownership、过期/接管 fencing、outbox 回滚及损坏存储的契约用例扩展到 v2；补充最终回答文本冲突与损坏文本/未知版本/摘要失败后禁止重问模型的验证。
- 新增提交后立即 os._exit 的真实进程故障场景，和正常退出一起验证 SQLite/PostgreSQL 的同入口恢复：文本、版本及摘要不变，零模型调用、零工具执行、零账本认领，Task 状态不变。
- 补充生产 RuntimePort 的最终回答转换与一次计量验证，覆盖空/非空工具快照；修正 PG 用例中的自比较断言，并同步设计及 delta spec。

## 实际命令与结果

所有 Python 命令使用本 worktree `.venv/Scripts/python.exe`。

| 范围 | 命令 | 结果 |
|---|---|---|
| 回归缺陷复现 | `python -m pytest packages/hecate-runtime/tests/test_port_decision_responder.py -k empty_tool_call_lists -q`（修正前） | 4 failed，证明空列表误拒 |
| 修正后定向回归 | responder、decision_step、agent_turn 及 Durable decision_store/agent_turn 五个文件 | 202 passed |
| 完整包测试 | `python -m pytest packages/hecate-runtime/tests packages/hecate-durable/tests -q` | 407 passed，7 skipped，0 failed |
| PostgreSQL 相关矩阵 | 配置临时进程环境中的 DURABLE_TEST_POSTGRES_URL，运行 decision_store、decision_pg、agent_turn 三个文件 | 143 passed，0 skipped，0 failed |
| 生产端口与边界 | `python -m pytest tests/test_core/test_port_decision_responder_adapter.py tests/test_core/test_runtime_port_cost.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_runtime/test_kernel_purity.py tests/test_layering_domain.py -q` | 32 passed，0 failed |
| 共享检查 | `python scripts/dev.py check all`，与 `bash scripts/dev.sh check all` 同一实现 | ruff check/format 通过（1613 文件）；mypy 959 源文件无错误 |
| OpenSpec | `openspec validate step6-accepted-final-answer --strict` | valid |
| 规划数据 | `python scripts/feature_inventory.py check` | 413 entries，0 errors，189 warnings |
| 差异格式 | `git diff --check` | 通过 |

PostgreSQL 矩阵命令：

```text
python -m pytest packages/hecate-durable/tests/test_accepted_decision_store.py packages/hecate-durable/tests/test_accepted_decision_pg.py packages/hecate-durable/tests/test_accepted_agent_turn.py -q
```

连接从已运行的 `hecate-step6-pg` 容器配置读取，仅传给测试子进程，不记录凭据。上述三个文件均经 decision_database 为每个用例创建并清理独立 schema，不操作业务 schema。

完整包测试默认未配置 PostgreSQL：6 个 decision_pg 用例条件跳过，已在上述真实 PostgreSQL 矩阵补验；另 1 个 worker_dispatch 的并发 writer 用例要求 PostgreSQL，此项不在最终回答相关矩阵内，保留条件跳过，不计作通过。规划检查的 warnings 为显式登记的治理字段及 evidence/acceptance 缺口；本次未修改 feature 数据文件。

## 保留边界

- v1 动作决策的摘要计算及 opaque accepted_output 语义保持，v2 显式要求空动作与非空白文本；未知版本拒绝。
- 最终回答是已接受的模型输出，不代表 Task 成功或目标达成；没有新增 Controller、自动续回合、Task/API/前端改动，也没有数据库迁移和新增依赖。
- 未运行真实模型质量评测、全仓 pytest 或 CI；本地使用确定性端口及 SQLite/PostgreSQL 验证，CI 留待后续 PR 流程执行。
