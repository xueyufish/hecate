# Tasks

## 1. 入口服务与关联基础

- [x] 1.1 实现 `EntryExecutionService`(`src/hecate/execution/entry_service.py`):组合 `WorkflowExecutionService` 与 `TaskRunRegistry`,提供 `execute`/`execute_stream`/会话续接窄接口;单元测试验证委托与登记调用顺序,验证不引入引擎具体 import
- [x] 1.2 实现按入口分档的关联模式:业务入口每请求 Task+Run+conversation 链接,评估入口单一内部 Task 复用;登记失败 fail-open 且结果显式带 `correlation: missing` 标记;测试覆盖两档关联与失败标记(含跨 workspace 拒绝沿用 task-run 语义)
- [x] 1.3 实现事件映射层:引擎流事件 → `EventEnvelope`(task_ref/run_ref、source_sequence、payload_schema_ref),原始事件保留为后端详情,tool 配对校验与未配对显式告警;往返/翻页/配对单元测试通过
- [x] 1.4 OpenAI/SSE 适配改造:`channel/api/v1/chat.py` 的流式转换输入改为映射后事件,chunk 字段集合钉住为既有 OpenAI chunk 模型;测试断言客户端可见输出无引擎内部字段

## 2. HTTP 入口切换

- [x] 2.1 `channel/api/v1/chat.py` 与 `agents.py` 的引擎路径改经入口服务(直连回退路径不动);真实 HTTP 回归:流式/非流式、`finish_reason`、响应 schema 与迁移前基准一致
- [x] 2.2 入口执行登记 Task/Run/conversation 链接;测试:同一 agent 经 HTTP 与(待迁移的)服务面执行可追溯到统一关联记录

## 3. MCP 入口切换

- [x] 3.1 `tools/mcp/server.py` 的 `agent_chat`/`session_resume` 改经入口服务,补齐 event_store/checkpoint 装配;测试:MCP 执行产出配对 `TOOL_CALL`/`TOOL_RESULT` 与 commit point,与 HTTP 等价
- [x] 3.2 MCP 会话续接回归:续接沿用会话既有路径与状态,不产生重复工具副作用

## 4. IM 与评估入口切换

- [x] 4.1 IM 注入对象换为入口服务(`channel/im/message_bus.py` 消费面保持);IM 消息执行回归通过并登记关联
- [x] 4.2 `ops/evaluation/engine.py` workflow 执行改经入口服务,评估运行关联单一内部 Task;评估回归套件通过,执行结果可按评估任务追溯

## 5. workspace 切流与放量记录

- [x] 5.1 实现生效开关解析:workspace 覆盖 > 全局 `CHAT_TOOL_LOOP_ENGINE_ENABLED` > 默认 false,复用既有 workspace 设置存储;解析顺序与默认行为测试通过
- [x] 5.2 覆盖变更写放量记录(workspace、actor、旧→新、时间),复用 audit-logs;审计可查询测试通过
- [x] 5.3 会话路径亲缘:会话首工具轮次记录生效路径,续接/恢复按记录而非重新解析;变更开关后存量会话续接不中途切换的测试通过
- [x] 5.4 全局默认保持 false 并在配置注释中说明放量纪律;workspace 维度行为差异测试(两 workspace 不同覆盖互不影响)

## 6. G3 真实入口证据

- [x] 6.1 真实 HTTP/SSE 多轮流式测试(不 mock 执行服务):两轮工具调用配对、中间迭代静默、最终答案 SSE 语义
- [x] 6.2 真实入口审批拒绝测试:拒绝记录传递到入口响应语义,与直连路径等价
- [x] 6.3 真实入口断线/恢复测试:流中断后经会话续接恢复,沿用既有路径与状态,无重复工具副作用
- [x] 6.4 取消语义测试:取消经入口服务的表现与引擎语义一致(HTTP 取消端点如需,随 step6 设计,见 design Open Questions);在 G3 证据记录中注明覆盖范围
- [x] 6.5 汇总 G3 证据指针到变更说明(每入口的端到端结果),声明未覆盖入口不得视为收敛

## 7. 分层、矩阵与文档同步

- [x] 7.1 分层测试:扫描入口层模块 import,禁止 `PregelRuntime`/`GraphCompiler` 具体符号;负例(注入违规 import)测试失败并指明模块与符号
- [x] 7.2 新增执行栈契约/依赖矩阵文档(`docs/design/`):平台/hecate-runtime/hecate-runner 兼容组合与支持窗口;CI 断言矩阵与 workspace/lock 实际版本一致
- [x] 7.3 计划文档同步:补勾 step5c 两项(证据 #208);step5d 相关行只登记本次切片(HTTP/MCP/IM/评估),A2A 与定时任务 executor 登记为未迁移绕过路径
- [x] 7.4 基线文档 §6 缺口表更新:A2A/定时任务绕过登记与本次切片完成记录

## 8. 验证

- [x] 8.1 全量四检:ruff check/format、mypy `src/`、受影响测试目录(HTTP/MCP/IM/评估/执行/场景清单)通过;`openspec validate platform-entry-migration --strict` 通过
- [x] 8.2 复核 specs 场景逐条有对应测试或显式理由;切片完成记录不勾选整体入口迁移项
