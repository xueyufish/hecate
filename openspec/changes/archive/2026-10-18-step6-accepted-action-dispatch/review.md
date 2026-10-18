# 复核结论

已在指定 feature worktree 直接修正，当前单动作派发切片通过本地复核。无未处理的阻断问题；没有提交、推送或归档。CI 和 PR review 仍需后续流程完成。

## 已修正问题

- **P1：执行身份可覆盖且作用域不完整。** 原实现把 execution_context 展开放在派生 session_id 后面，换临时会话即可重复执行业务；原 Run 字符串的分隔符拼接存在碰撞；执行身份也没有包含 decision_step_id。补充四个回归用例对原实现全部失败。现规范 JSON + 固定 UUID namespace 绑定 Run/步骤/动作键，使用 UUID 兼容的 session，强制覆盖上下文的保留身份，并返回稳定 execution_id。
- **P1：ownership/持久账本是可选门。** 原实现允许不注入 validator，并可通过无 ActionLedgerHook 的 executor 直接执行业务；原 SQL 桥测试正是这种装配。现新入口必须有 ownership 和同一实际执行 hook 的绑定校验；旧 hook 默认拒绝新路径。SQL 在恢复前、审批后的领取及业务边界核实 Task/Run、hook lease 与权威 lease 的一致性，含 holder/精确 token/期限/已有动作归属。缺失和 stale 的 owner 不能借 readonly 路径降级放行。
- **P1：回执丢失仍被报告为成功。** 原 step 会继承普通 executor 的 warning/通道兼容行为。现仅新 accepted 入口将 outcome 写入失败或调用后无法确认的异常转换为 stopped/reconciliation_required，后续恢复不重复写副作用，旧入口行为保留。
- **P2：状态从 is_error 推断。** 原业务失败被分类为 withheld；现控制流明确区分 succeeded/failed/withheld，ExecutionStoppedError 保留停止类别。它们都不是 Task 成功判断。
- **P2：原参数和真实结果可能被上下文替代。** 新路径拒绝 pre hook 静默改写已接受参数，恢复不从外部传入的 messages 回填，防止伪造历史盖过持久结果。
- **证据缺口：内存实例重建冒充跨进程。** 保留的内存用例改名为组件重建；新增两个真正独立 Python 进程在 SQLite/PostgreSQL 上恢复：首进程提交 outcome 后 os._exit，后进程禁止模型和业务再调用，fsync 效果计数文件恰一行。

## 验证

- 原 GLM 新增用例：16 passed。
- 原实现增加身份/缺账本回归：4 failed、14 passed，确认缺陷可复现。
- 完整受影响 runtime/durable package 与旧工具恢复/自足/分层回归：344 passed、6 skipped。
- 新 SQL 派发矩阵：26 passed，SQLite 与 PostgreSQL 各 13 项，无跳过，包含独立进程恢复。
- 共享检查：ruff/format/mypy 全通过（1596 files 格式合规，949 source files 类型检查通过）。
- 严格 OpenSpec 校验和 git diff --check 通过；未跟踪的新代码/artifacts 另行检查尾部空白。

详细命令和跳过项解释见 verification.md。曾因工作环境未安装 psycopg 无法初始化 PG 测试，已通过声明的 locked postgres profile 同步修复，并完成真实矩阵；未把该初始化失败或跳过记作通过。

## 范围

改动只在 runtime/durable 的派发模块、共享 executor/hook 新入口、对应测试与本 change artifacts。未修改平台 Task 查询/API、evaluation/versioning、schema、公共 conftest 或其他 worktree。

这不是完整 Controller、批次部分成功编排、入口路由或生产治理认证；没有真实模型请求。不承诺外部动作 exactly-once，未确认结果保持待对账。
