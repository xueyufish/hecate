# Proposal

## Why

对 `main`(`9b9dae5`)的代码核对发现三处状态记录与事实不符:归档 change `complete-step6-recovery-gates` 的 Task2/Task3 声称已交付的能力超出代码与验收证据;演进方案 step6c 的关闭门槛文本仍把已有代码的受管命令链列为待实现。不先修正这些记录,后续收口 change 的验收表述就没有正确的锚点,且会重复"组件测试当作完整验收"的错误。

## What Changes

- 演进方案 `docs/refactor/enterprise-agent-platform-evolution-plan.md` step6 节:step6c 条目补记受管命令下发、宿主处理、效果回执与新 attempt 关联的代码已随 #232 交付(`managed_channel.command_for_host`、runner `_apply_command`/effect 上传),剩余为真实受管进程全链验收与 step7 合法审批判定。
- 归档 `openspec/changes/archive/2026-10-08-complete-step6-recovery-gates/tasks.md`:为 Task2("完成内置 continuation")与 Task3("接入真实工作流父子等待")追加带日期的更正注记——平台侧仍以新 attempt 重放 + 保守待对账为主(`task_dispatcher.py` 对受保护中断转 reconciliation,`builtin.py` 标记 continuation controls unsupported),原生 continuation 未实现;生产代码不存在提交子任务并持久等待的 workflow 节点或具名 adapter,现有等待测试经 monkeypatch 替换 `_execute`。**不改写原有勾选,只追加注记。**
- `docs/refactor/step6-followup-review.md` 追加带日期的"状态记录修正"小节:登记上述两处超额声明的代码证据、step6c 文本滞后,以及后续收口 change 拆分(受管命令验收、内置原生 continuation、真实父子 adapter、Lease 续租策略、PG 进程矩阵)。

不修改任何代码、测试或契约;不翻转 step6b—6f 复选框(翻转随各自收口 change 的验收证据进行)。

## Capabilities

### New Capabilities

(无——本 change 为纯文档状态修正,`.openspec.yaml` 声明 `skip_specs: true`。)

### Modified Capabilities

(无——不修改任何能力 spec 的需求。)

## Impact

- 受影响文档:`docs/refactor/enterprise-agent-platform-evolution-plan.md`(step6 节)、`docs/refactor/step6-followup-review.md`(追加小节)、归档 tasks.md(两行注记)。
- 不影响运行时行为、数据库 schema、API 或测试结果;`openspec validate` 与文档一致性检查须通过。
- 风险:修正后的状态必须准确引用代码位置,避免引入新的表述错误;注记不改变归档历史记录的其他内容。
