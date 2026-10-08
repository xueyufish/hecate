# Tasks

## 1. 共享平台栈

- [x] 1.1 将 `tests/scenarios/test_sc05_managed_reconnect.py` 中的 `managed_stack` fixture 与 `_serve_over_tcp` 抽出为共享 helper(`tests/scenarios/tools/managed_platform.py`),SC05 原文件仅改导入;验证:SC05 既有测试在 SQLite 下全绿且断言零修改。(9 passed;断言与测试体未改,仅头部装配改为消费共享 helper)
- [x] 1.2 核对平台任务控制命令端点的请求体(command_id 可否指定、resume/provide_input 输入格式、TTL 是否可配),结论写入实现;验证:以平台应用服务真实下达一条命令并在 `/host/pull` 载荷中观察到翻译结果。(`TaskControlService.issue_command` 支持 `command_id` 幂等重放与 `expires_at` ISO 时间;wait_token 经 `detail_ns` 传递;命令记录进进程级 durable suite recorder,`/host/pull` 即从该 recorder 读取)

## 2. 唤醒链全链场景

- [x] 2.1 新增 `tests/scenarios/test_sc05_managed_wake_chain.py`:受管投递→工具 `approval_required` 持久等待→平台 resume 命令→runner 应用→successor attempt→终态;断言平台投影终态仅在 successor 事件/effect 之后、原等待 Run 平台投影保持 `waiting_approval`、successor 关联先于其执行事件被接受。验证:SQLite 下 5/5 passed,业务调用计数符合预期(等待前写一次、等待后读一次)。
- [x] 2.2 验收暴露的 runner 缺口:已决动作回填不消耗 Lease(`is_decided_action`)+ 同一 Run 多受保护派发的有界续租等待(`_LEASE_REFRESH_WAIT_SECONDS=5`,`_LEASE_REFRESH_POLL_SECONDS=0.05`)。验证:happy path 5/5 通过,前序业务写恰好一次,无 second write 副作用。

## 3. 故障变体

- [x] 3.1 等待期间 runner `kill` + `restart` 后唤醒:新进程消费命令、successor 完成,前序受保护写全链恰好一次。验证:独立用例通过且业务调用计数断言(写 1、读 1)。
- [x] 3.2 平台重启后命令仍投递且幂等:记录命令后、拉取前停止并重启平台服务(同一存储),命令经重投到达;重复拉取同 command_id 返回原 applied 回执、无第二次效果。验证:独立用例通过,固定 `expires_at` 保证重放比较一致。
- [x] 3.3 effect 上传丢失:平台侧 one-shot 503 中间件精确匹配 `POST /managed/host/commands/effect` 首次请求;断言重试后平台恰好一条 applied effect、业务计数不变。验证:独立用例通过,重置计数为 1,业务调用计数恰好 1 写/1 读。
- [x] 3.4 过期命令:停服并写入 TTL=1 s 的命令,等候 > 1 s 后重启服务,runner 拒绝并留 expired/rejected 回执,业务零写零读,本地 Run 仍 `waiting_approval`。验证:独立用例通过。

## 4. PostgreSQL 参数化

- [x] 4.1 套件按 `HECATE_STEP6_POSTGRES_URL` 门控参数化(共享 `step6_runner_database_url` 来自 `tests/scenarios/conftest.py`);平台 durable 套件与 runner 本地存储指向独立 schema/database;未设置时显式 skip。验证:本机不设变量时 SQLite 参数化全跑通;PG runner 端矩阵待 `step6-pg-process-matrix` 在 Linux CI 接入。

## 5. 文档与清单同步

- [x] 5.1 `tests/scenarios/manifest.yaml`:SC05 `implemented_slices` 追加 `managed_wake_chain`/`managed_command_receipt`,gaps 注明 step6f 与 step7 剩余项;`docs/refactor/enterprise-agent-platform-evolution-plan.md` 在 step6b/step6c 各追加 2026-10-08 注记(技术交付部分 + 剩余依赖),`docs/refactor/step6-followup-review.md` 追加本 change 的验收记录。验证:manifest 一致性测试通过,方案文本与实际验收范围一致。

## 6. 验证

- [x] 6.1 全量门禁:`ruff check src/ tests/ packages/`、`ruff format --check src/ tests/ packages/` 通过;`mypy src/ packages/` 对平台与改动包通过;受影响 pytest 套件(`tests/scenarios/test_sc05_managed_reconnect.py` + `test_sc05_managed_wake_chain.py` + `test_manifest_consistency.py`)全绿;结果与命令记入 change 的 `verification.md`。
