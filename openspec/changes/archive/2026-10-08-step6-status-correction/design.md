# Design

## Context

见 proposal.md。本 change 只修正状态记录,代码事实来自对 `main`(`9b9dae5`)的静态核对:平台 dispatcher 对含受保护动作的中断转 `reconciliation_required` 并以新 attempt 重放(`src/hecate/execution/task_dispatcher.py`),builtin 后端中断标记 `continuation controls are unsupported`(`src/hecate/execution/builtin.py`);生产代码中父子任务等待只有消费侧(回调核实与唤醒,`task_control.py` / `channel/api/tasks.py`),无生产侧 adapter,等待测试经 monkeypatch 替换 `PlatformTaskDispatcher._execute`;受管命令链(平台命令翻译、宿主命令 inbox、effect 回执上传、新 attempt 关联)已有代码(`managed_channel.py`、`hecate_runner/managed.py`)。

## Decisions

1. **归档历史用追加注记,不改写勾选。** 归档 tasks.md 是带时间戳的历史记录;直接撤销 `[x]` 属于改写历史。采用本仓库既有模式:在 Task2/Task3 行下追加一行带日期的更正注记(以"更正(2026-10-08):"开头),原始声明保持原样。
2. **修正三处,不扩大范围。** (a) 方案 step6c 条目补记已有代码;(b) 归档 Task2/Task3 注记;(c) `step6-followup-review.md` 追加小节承载完整证据与后续拆分。方案 6d/6e 条目本身表述诚实(原生 continuation 与真实 adapter 已列为关闭门槛),不动;step6b—6f 复选框不翻转,翻转随各自收口 change 的验收证据进行。
3. **后续收口拆分登记在复核报告,不写入方案新章节。** 五个后续 change(`managed-command-acceptance`、`builtin-native-continuation`、`workflow-child-adapter`、`lease-renewal-policy`、`step6-pg-process-matrix`)及依赖关系记录在追加小节中,作为实施顺序建议;避免方案正文承担实时状态源职责。

## Non-Goals

- 不修改任何代码、测试、契约或场景清单状态。
- 不重新评审或推翻已合入的实现本身(仅修正其完成状态表述)。
- 不提前翻转任何 step6 切片的完成标记。

## Risks / Validation

- 验证:`openspec validate` 通过;引用的代码位置在 `main` 上可核对;归档文件 diff 仅含两行追加注记;方案 diff 仅限 step6c 条目与"主线追加复核"指引句。
