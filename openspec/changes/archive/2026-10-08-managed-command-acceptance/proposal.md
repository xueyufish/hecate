# Proposal

## Why

方案 step6c 的剩余门槛(经 `step6-status-correction` 更正)为"真实受管进程的全链验收(等待→命令→唤醒→新 attempt→终态、两端重启、确认丢失、重复/过期命令)与 step7 合法审批判定"。代码侧链路已随 #232 交付(平台命令翻译 `command_for_host`、`/host/pull` 命令载荷、runner 命令 inbox `_apply_command`、effect 回执端点、successor attempt 关联端点),但既有测试只在组件/ASGI 层覆盖命令投递与拒绝回执:`tests/test_execution/test_managed_execution_loop.py` 无任何命令场景,SC05 覆盖重复命令不重复执行但不含唤醒链。技术闭环缺少进程级证据,无法翻转方案条目。

## What Changes

- 新增真实进程全链验收套件:安装后的 Runner wheel 对接真实平台 HTTP(TCP,非 ASGI),覆盖"受管投递→工具进入 waiting_approval/waiting_input→平台下达 resume/provide_input 命令→runner 经持久命令执行器唤醒→successor attempt 执行→终态→事件/effect 回执/successor 关联上传→平台投影终态"的完整链路。
- 故障变体(同一套件内):等待期间 runner kill/restart 后唤醒仍成立且前序写入零重做;平台重启后命令仍投递且幂等;effect 上传丢失重试不重复应用;重复 command_id 返回原回执;过期命令拒绝且无副作用。业务侧断言使用真实业务 API 调用计数。
- 套件按存储参数化:默认 SQLite 本地可跑;设置 `HECATE_STEP6_POSTGRES_URL` 时同一套件在 PostgreSQL 上运行(为 step6f `step6-pg-process-matrix` 复用;未设置时显式 skip,不冒充通过)。
- 修复验收中暴露的命令链缺口(已发生两处,均在 runner 侧):已决动作回填不消耗 Lease;同 Run 多受保护派发的有界续租等待。不做 step7 范围的企业审批判定/授权语义。
- 文档同步:方案 step6b/6c 条目追加技术交付注记并保留 step7 依赖行;SC05 manifest 的 `implemented_slices`/`gaps` 按实际覆盖更新(场景整体状态不因组件级通过而翻转)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `managed-runner-enrollment`: 新增"受管唤醒全链仅经真实进程验收关闭"需求——现有三条命令相关需求(命令回执先于投影、successor attempt 可见、真实 HTTP 验收关闭投递门槛)覆盖组件语义与投递门槛;本变更补上唤醒链的进程级关闭条件与故障场景集,并声明 PostgreSQL 参数化复用方式。

## Impact

- 测试:`tests/scenarios/`(新增受管唤醒链场景测试,复用 `runner_harness.py` 与 SC05 的 `managed_stack`/`_serve_over_tcp` 模式)、`tests/scenarios/manifest.yaml`(SC05 slices/gaps)。
- 产品代码:仅在验收暴露缺口时最小修复(实际涉及 `packages/hecate-runner/src/hecate_runner/managed.py` 相邻的 `durable.py`、`engine.py`);无 schema/迁移变更。
- 文档:`docs/refactor/enterprise-agent-platform-evolution-plan.md`(step6b/6c 条目)、`docs/refactor/step6-followup-review.md`(验收记录);不改变其他步骤状态。
- 不涉及 step7 企业授权/审批判定、中心证据缓冲(step10)。
