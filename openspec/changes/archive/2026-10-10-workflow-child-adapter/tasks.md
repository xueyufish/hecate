# Tasks

## 1. 具名 adapter 与 dispatcher 接入

- [x] 1.1 新增 `src/hecate/execution/workflow_child.py`:`WorkflowChildTaskAdapter`——解析输入载荷的 `workflow.steps`、提交子任务(系统发起 + 父引用盖章)、产出 `TaskWaitingSignalError` 等待契约、从持久输入推进步数。验证:adapter 单测(步骤解析、盖章字段、推进计数不依赖进程内存)。
- [x] 1.2 dispatcher 接入:`_execute` 识别 workflow 载荷并经 adapter 提交/等待;`_finish` 子终态钩子自动发起 `submit_workflow_callback`(平台 issuer、幂等 command_id、失败不阻断子任务终态)。验证:单测覆盖钩子触发与幂等;既有 dispatcher 回归零断言修改通过。

## 2. 端到端集成验收(真实路径,无 `_execute` monkeypatch)

- [x] 2.1 单步链:声明一步的父任务 → 真实派发 → 父挂 `waiting_input`(契约绑定子任务)→ 子任务 succeeded → 自动回调核实 → 父唤醒且持久输入含子结果摘要 → 父 succeeded。验证:集成测试经真实 dispatcher 与 worker。
- [x] 2.2 多步顺序:两步声明 → 第一步唤醒前第二步子任务不存在(未提交)→ 逐步推进、顺序严格;伪造/跨 workspace 回调被拒(消费端既有语义回归)。验证:集成测试。
- [x] 2.3 失败收敛:`on_failure=fail` 下子任务失败 → 父任务失败、剩余步骤不提交;`on_failure=await` → 父任务转待对账。验证:集成测试。
- [x] 2.4 双端重启:父等待期间重启父平台进程、子任务在另一 dispatcher 实例完成后,自动回调跨进程收敛;唤醒后父进程再重启,推进计数不回退、无重复提交。验证:集成测试(独立 dispatcher 实例 + 时钟推进)。

## 3. 文档同步

- [x] 3.1 演进方案 step6e 条目按验收证据翻转;`docs/refactor/step6-followup-review.md` 追加验收记录(含 2026-10-08 修正表 Task3 行的取代说明)。验证:方案文本与运行证据一致。

## 4. 验证

- [x] 4.1 门禁:`ruff check src/ tests/ packages/`、`ruff format --check`、`mypy src/ packages/` 全绿;`tests/test_execution/` 全目录回归通过;OpenSpec strict 校验通过;CI 全绿。(2026-10-10 补登:本地门禁全绿——509 passed/30 skipped、mypy 893 文件零错、strict 通过;CI 随分支首次推送/合并确认,证据见本 change verification.md)
