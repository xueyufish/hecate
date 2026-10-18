# 验证记录 — step7-shared-tool-call-executor

本文件记录复核修正后的实际结果，替代初版与用例不匹配的验证声明。结论限于共享组件提取与既有行为回归；不代表完整 Controller、决策动作派发或生产认证。

## 共享检查

使用本 worktree 的 .venv/Scripts/python.exe：

- scripts/dev.py check all：ruff check 全过；1588 个文件格式检查通过；mypy 检查 943 个 source files，无错误。
- 最后补入自足探针清单后，对该文件再次执行 ruff check 与 format --check，均通过。
- OpenSpec strict validation 与 git diff --check 通过。

## 双入口与边界证据

packages/hecate-runtime/tests/test_tool_call_executor.py 共收集 56 项：双入口参数化 52 项，批次顺序/异常排空 2 项、冷进程解耦 1 项、旧 sandbox 路径同实现 1 项。以上均在最终组合回归通过。

| 场景 | 实际证据 |
|---|---|
| 真实公开入口对等 | 同组用例分别调用 ToolCallExecutor.execute_call 和 ToolWorker.execute；不再只直呼 Worker 私有委托 |
| provenance 与事件 | 不提供 superstep 可执行并记录 TOOL_CALL/TOOL_RESULT；缺省为 0，显式 superstep/trace 原样保留；事件 node_id 仍为 None；EvidenceTracker 记录调用方标签 |
| 策略与审批 | 拒绝、无 callback、审批拒绝/通过、invocation/approval 绑定、非法 JSON/非有限参数 |
| hooks/middleware 与 sandbox | legacy pre BLOCK、legacy post SANITIZE、chain pre BLOCK、chain post SANITIZE、chain 优先级、sandbox enforcement 路由 |
| 失败与未知结果 | 普通异常为 failed；超时/连接异常为 unknown，恢复停止且不再次派发 |
| 账本恢复与故障 | claim 先于业务、outcome 记录真实结果，无事件历史回填；lookup/claim 不可用按既有类别规则处置；outcome 写失败后不重复受保护动作 |
| 并发竞争 | 两个预检被屏障强制同时 miss，两个 claim 竞争，只有一个业务调用和真实 outcome |
| 批次收尾 | 业务调用被显式挂起，另一调用 unknown 后 Worker 仍未返回，释放后排空再上抛；另强制逆序完成核对结果顺序 |
| 冷进程解耦 | 子进程在首次 runtime 导入前禁止 Pregel/compiler/channel/Worker/workers/scheduler/checkpoint，构造共享组件，使用真实 InMemoryEventStore 执行只读调用；不受父进程缓存影响 |

tests/test_runtime/test_action_hook.py 新增 4 项 SQL 集成用例，直接 executor/Worker 两入口分别验证：
- store、hook、executor 均关闭重建，没有 EventStore/通道历史时回填真实 SQL 结果；
- 模拟业务成功后回执写失败，再重建时停止待对账；
- 同一逻辑动作始终仅发生一次业务调用。

这四项是对象与连接重建证据，不宣称真正跨进程持久恢复认证。

## 最终回归命令与结果

组合命令：

    .venv/Scripts/python.exe -m pytest packages/hecate-runtime/tests packages/hecate-durable/tests packages/hecate-runner/tests tests/test_runtime tests/test_layering_domain.py tests/test_execution/test_approval_wait_park.py tests/scenarios/tools/receipt_harness.py --deselect=tests/test_runtime/test_hooks/test_shell_hook.py::test_shell_hook_json_stdin -q

结果：1935 passed, 7 skipped, 1 deselected，耗时约 302 秒。包含一条 FastAPI 422 状态码名称弃用警告。跳过项按其既有条件处置，不计为通过；本轮未启用可选 PostgreSQL 环境，不据此宣称 PG 验证。

补充命令：

- tests/test_runtime/test_runtime_self_sufficiency.py + tests/test_runtime/test_action_hook.py：15 passed。该次执行包含最后加入探针清单的共享模块以及 4 项 SQL 重建测试。
- 临时将已安装的 C:/Program Files/Git/usr/bin 加入当前测试进程 PATH，再运行唯一 deselected 的 shell hook JSON stdin 用例：1 passed。未修改系统环境或该测试断言。

组合回归与独立 shell 用例合计覆盖 1936 个通过项、7 个条件跳过项，不重复累计补充定向测试。

## 环境失败调查

初次扩大回归发现 shell JSON stdin 测试在当前 Windows shell 下找不到 cat。直接从 git HEAD 加载未修改的 ShellCommandHook，得到相同 command-not-found 与空输出；补齐已安装 Git Bash 工具目录后原断言通过。未以“既有失败”跳过调查，也未修改无关业务代码。

初次复核还复现聊天装配属性缺失，以及 tenant attribution 测试向已失效 Worker 字段注入策略的问题。修正测试观察/注入位置后，原行为断言通过。不存在未处理的本 change 失败。

## AST 与兼容核对

- 12 个迁移方法逐个对比 HEAD：除 docstring 和声明的 superstep 缺省适配外，AST 一致。
- Worker 的 execute、_extract_tool_calls、_apply_citation_provenance 与 HEAD AST 完全一致。
- sandbox router 原实现移至共享位置后与 HEAD AST 完全一致；旧路径转发同一模块/类。
- 原构造签名、tool_worker 恢复符号 re-export、平台兼容 shim 及既有私有单次薄委托保留。
- Runtime 业务自足与 layering/purity 回归通过，没有 durable/Runner/平台反向依赖。

## 保留范围

未提交、推送、创建 PR 或归档；CI 与 PR review 留待正常提交流程。未新增动作账本、扩展接口、数据库 schema、决策派发、多轮 Controller、持久审批等待或聊天入口切流。
