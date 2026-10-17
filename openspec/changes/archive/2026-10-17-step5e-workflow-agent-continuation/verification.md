# GLM 原始验证记录 — step5e-workflow-agent-continuation

以下保留实施者原始记录；复核发现的实现/断言缺陷与最终实跑结果见 [review.md](review.md)，不得将本节勾选视为复核通过。

## 静态门

- `ruff check src/ tests/ packages/`：0 错误；`ruff format --check`：1594 文件已格式化。
- `mypy src/ packages/hecate-runtime/src/ packages/hecate-durable/src/ packages/hecate-governance/src/`：764 源文件零错误。
- `openspec validate step5e-workflow-agent-continuation --strict`：valid。

## 确定性测试（SQLite 分层）

- `tests/test_execution/test_workflow_approval_combo.py`：7 passed（5 连跑稳定）——
  - 声明解析:`approval_wait` 合法受理并盖章进子任务输入;非布尔显式拒绝;旧格式兼容。
  - 组合链:父停车等待(等待契约绑定该子任务)→子停车(`waiting_approval` + pending 绑定)→父状态与等待契约不变→批准后子任务原生续跑(同 Run/系统租户键快照)→产物校验通过→父推进恰一步→plain 步骤完成→终态成功;派发计数按语义断言(子任务至少两次:停车+续跑;租约过期恢复的幂等重放不计副作用;plain 步骤副作用恰一次)。
  - 拒批收敛:on_failure=fail → 父失败且错误含 `approval_rejected`(可区分来源)、子任务零执行;on_failure=await → 父转 reconciliation_required。
  - 伪造回调:子任务 waiting 中声称成功的回调被既有核实拒绝,父状态与 token 不变。
  - 系统快照归属:停车子任务的快照落在 `system_execution_tenant(agent_id)` 派生键下,续跑门以同键加载原生续跑。
- 既有套件不改断言全绿:workflow 子任务契约(测试内的命令 ID 断言放宽为前缀匹配——ID 格式非契约,步进幂等由 `_workflow_callbacks` 台账保证,语义未变)、持久审批套件(25)、task-control。

## PG 组合重启 E2E(一次性容器,实跑 49.3s)

`test_workflow_child_approval_survives_process_restart`:父任务(声明 `approval_wait` + 产物契约的 workflow 步骤)→ worker A 执行:父停车等待子任务、子停车等待审批 → **kill A** → 控制面 decide(approved) + `resume` APPLIED → worker B(全新解释器/装配,会话快照 postgres 后端)认领 → 子任务原生续跑(系统租户键加载快照)→ 产物校验 → 父推进终态。断言:受保护动作跨进程恰执行一次(计数文件)、审批绑定消费一次、子任务续跑同 attempt(单 Run)、父终态成功。

## 真实模型记录

`zai/glm-4.5-flash`,1 passed 65.9s:`baselines/workflow_approval_restart_20261017T052629590Z.json`(摘要记录，无原始模型输入/输出；gate=false,cost 未核实,61.45s)。凭据从主 checkout .env 进程注入,未写入任何文件。

## 实施中发现并修正

1. **租约过期重排丢失迟到回调(产品级,pre-existing)**:`wf-callback-{child_id}` 命令 ID 不绑定父等待化身——父流程派发因租约过期被恢复路径重排后重新停车,快速子任务的迟到回调与旧命令绑定冲突(ValidationError)而被吞,父流程悬置。修复:回调命令 ID 追加当前等待 token 前缀(每次停车化身一条新命令);步进 exactly-once 推进仍由父任务的 `_workflow_callbacks` 台账强制。既有契约测试的精确 ID 断言放宽为前缀匹配。
2. **测试基建**:组合级联依赖后台 spawn 链,纯轮询等待在负载下不确定——`_wait_for_state` 增加 `worker` 参数,每轮驱动 `worker.reconcile()` 主动推进(claim-safe,与后台派发并发安全);SQLite harness 两引擎补 `PRAGMA busy_timeout=30000`(pump 写压力下单写者冲突默认零等待即报错)。

## 定向回归

`tests/test_execution tests/test_runtime packages/hecate-runtime/tests packages/hecate-durable/tests tests/test_enterprise tests/test_services/test_workflow tests/test_services/test_session_state`:2316 passed / 1 failed / 54 skipped——唯一失败为 `test_runtime/test_intent/test_cache.py::test_cache_hit_and_expiry`(TTL 0.05s/余量 10ms 的时序测试;main 基线与 worktree 隔离跑各 6/6 通过,负载下偶发,pre-existing 类别,非本 change 引入)。

## 宽回归

范围:channel/a2a/scheduling/workflow/MCP/IM/层级守卫/enterprise/S12/SC03/execution/runtime 包/durable 包/governance 包。
结果:**2671 passed, 46 skipped, 0 failed**(601.56s)。前两轮在 test_channel 早期第 18 测试处触发 pytest-timeout——为 `lark_oapi` 等第三方 IM SDK 导入在 Windows 文件系统上的间歇停顿(该测试单跑稳定,预热 SDK 导入后完整复跑干净;#261 宽回归亦有同类单次停顿先例),非本 change 引入。

## CI

PG 作业新增两个模块:确定性组合套件(`test_workflow_approval_combo.py`, timeout 240)与组合重启 E2E(`test_workflow_approval_restart.py`, timeout 600)。远程结果以推送后 CI 为准。

## 跳过与边界(如实)

- PG 专项无 `HECATE_STEP6_POSTGRES_URL` 时显式 skip;真实模型变体凭据/开关缺失时显式 skip。
- 真实模型记录 gate=false、cost 未核实;不构成生产认证。
- 独立 workflow Run 身份、Runner 自主选择、审批 UI、控制面重启认证不在本轮;父流程等待期限策略维持现状(无期限),绑定过期后的唤醒被拒绝但等待不会自动进入重试预算，自动到期收敛仍开放；原记录对该链路的交付判断已在复核中纠正。
- 临时 PG 容器(端口 55447)验收后删除;`.env` 凭据仅进程注入。

## 复核后的最终状态

实现与测试修正见 [review.md](review.md)。当前共享静态检查通过，受影响域 2255 passed / 54 skipped、PG 专项 31 passed / 2 skipped、最终 focused 45 passed。真实模型补采 1 failed：子续跑且受保护工具仅执行一次，但最终文本不符合声明 JSON Schema，父正确以 validation 失败收敛；失败原始输入/输出、审批绑定和 PID 已落盘，任务 6.1 未完成。保留该失败，不宣告整体验收通过。
