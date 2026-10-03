# Tasks

## 1. 入口侧共享装配收敛

- [x] 1.1 新增 `src/hecate/core/composition/entry_assembly.py`:`get_shared_event_store()`(惰性单例槽位)、`register_shared_event_store(store)`(幂等注册)、`build_tool_registry(...)`、`load_agent_tools(...)`(自 `channel/api/v1/chat.py` 迁移实现,英文 docstring)
- [x] 1.2 `core/composition/wiring.py` 在 lifespan 创建 `app.state.event_store` 后调用 `register_shared_event_store`,HTTP 路径与后台入口共用同一实例
- [x] 1.3 五个入口改造调用点:`channel/api/v1/chat.py`(引用新位置并保留行为)、`core/composition/im_entry.py`、`tools/mcp/server.py`、`channel/a2a/server/executor.py`、`ops/scheduling/executors.py`;删除 `im_entry._get_shared_event_store`、`mcp/server._get_shared_event_store` 与 chat 模块内旧私有实现
- [x] 1.4 `tests/test_layering_entry_imports.py` 增加断言:入口目录不得定义模块级事件存储单例,不得导入其他入口的私有装配函数;附跨入口共享同一存储实例的行为测试(memory 后端下经一个入口写入、另一入口视角可读)

## 2. A2A agent 解析收紧与错误响应治理

- [x] 2.1 配置新增 A2A workspace 作用域项(默认空),executor 按作用域唯一解析 agent:为空、零命中或多命中均返回 `TaskState.FAILED` 的 Task(稳定文案),不做全局查询
- [x] 2.2 执行异常分支改为稳定失败文案 + `logger.exception`,响应不含异常文本/内部细节;保留 Task/Artifact 字段集合与状态枚举不变
- [x] 2.3 更新 `tests/test_a2a/test_server/test_executor_entry.py`:补无配置拒绝、作用域外 agent 不可选中(构造第二个 workspace 的 agent)、多命中拒绝、失败响应无内部文本四个用例
- [x] 2.4 在 runner/部署文档或配置注释中标注新配置项与行为变化(未配置时 A2A 执行拒绝)

## 3. G3 断线/恢复真实入口测试

- [x] 3.1 在 `tests/test_channel/` 增加两段式会话测试:第一段流式执行中途放弃消费,第二段以同一 session 经真实 HTTP 入口恢复;断言状态延续、事件连续、tool call/result 配对完整、无重复执行副作用
- [x] 3.2 若实现阻塞(如放弃消费未触发 checkpoint),交付最小修复并在测试旁注明语义边界;不得放宽断言换取通过

## 4. 复核报告与方案清单处置

- [x] 4.1 撰写 `docs/refactor/step5-review-report.md`:复核范围与证据、发现与处置表(事件存储分片、A2A 全局选取、错误泄露、G3 断线/恢复缺失)、5d 剩余项处置映射(调度器接线/订阅视图/Pregel 映射尾项 → step6;A2A per-agent 身份、studio 测试入口 → 后续条目)、验证记录
- [x] 4.2 更新 `docs/refactor/enterprise-agent-platform-evolution-plan.md` step5 清单:5d 条目补处置注记(保持未勾选),G3 行补断线/恢复证据状态
- [x] 4.3 复核报告明确边界:memory 后端单进程语义、多 worker 不共享为既有事实、静态扫描绕过面、A2A 拒绝语义的迁移动作

## 5. 验证

- [x] 5.1 `ruff check src/ tests/`、`ruff format --check src/ tests/`、`mypy src/` 零错误
- [x] 5.2 受影响测试全绿:入口四套件(test_execution/test_entry_*、test_channel、test_mcp、test_a2a/test_server)、test_layering_entry_imports、test_services/test_scheduling、test_services/test_mcp_server;`openspec validate step5-review-hardening --strict` 与 `openspec validate --specs` 通过
