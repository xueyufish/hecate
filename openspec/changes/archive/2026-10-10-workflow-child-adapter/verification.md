# workflow-child-adapter — Verification

> 状态:实现与本地验收全部交付;CI 随分支首次推送/合并(PR)运行标准套件,
> 绿灯即为本记录的形式确认。studio 工作流 DSL 的节点类型扩展、跨 workspace
> 子任务与并行扇出不在本 change(见 proposal 的明确不做)。

## Evidence(本地)

- 端到端集成(`tests/test_execution/test_workflow_child_adapter.py`,真实
  dispatcher + 真实 entry 执行,无 `_execute` monkeypatch,4/4 绿):
  1. `test_single_step_workflow_parks_child_and_parent_succeeds` — 父任务挂
     `waiting_input`(契约 `await_task_ref` 精确绑定真实子任务、token 未消费)
     → 子任务真实执行 succeeded → 自动核实回调使父任务 requeue(token 恰好
     消费一次)→ 父任务 succeeded,持久输入含 `step_index`/`child_outcome`,
     子任务数恰为 1;
  2. `test_multi_step_order_and_forged_callback_rejected` — 两步严格顺序
     (第一步唤醒前第二步不存在);伪造回调(不存在的子任务)被
     `REJECTED`、token 不消费、父任务保持等待;最终 `step_index == 1`;
  3. `test_failed_step_converges_per_on_failure` — 子任务执行失败
     (LLM stub 抛错→失败终态→自动回调声明 failed):`fail` 声明下父任务
     `failed`、`await` 声明下父任务 `reconciliation_required`,剩余步骤均
     不提交;
  4. `test_both_side_restarts_keep_the_chain` — 五个独立 dispatcher/worker
     实例接力完成两步链(父等待→子完成跨实例回调→父推进→再等待→再完成→
     收敛),子任务数恰为 2,重启零重复提交。
- 实现层发现并修补:`submit` 持久化载荷为固定字段白名单,
  `workflow`/`workflow_parent` 初版被静默丢弃(任务降级为普通执行)——已补
  passthrough 并加提交时快速校验(畸形声明 `TaskControlValidationError`)。
- 回归:`tests/test_execution/` 全目录 509 passed/30 skipped(含既有
  monkeypatch 原语测试零修改——保留为契约层验证);ruff check/format 全绿;
  `mypy src/ packages/` 893 文件零错;OpenSpec strict 校验通过。

## Gates

```bash
ruff check src/hecate/ tests/test_execution/test_workflow_child_adapter.py
ruff format --check src/hecate/execution/ tests/test_execution/test_workflow_child_adapter.py
mypy src/ packages/    # Success: no issues found in 893 source files
pytest tests/test_execution/ -q   # 509 passed, 30 skipped
openspec validate workflow-child-adapter --strict   # valid
```

## Honest scope

- 6e 关闭门槛(真实父子执行 + 各自进程重启)已交付;monkeypatch 原语测试
  保留为契约层验证,不再是唯一证明。
- studio 工作流 DSL 的节点类型扩展(编排产品入口)、跨 workspace 子任务、
  并行扇出、每步 agent 覆盖未做(需要时另起 change)。
- 步骤失败即收敛(不走 worker 重试预算)是编排语义决定,Transient 类失败
  的重试策略归父任务的 on_failure 声明与后续策略。
- Step6 剩余:6f(PostgreSQL 宿主进程矩阵)与 step7 依赖项。
