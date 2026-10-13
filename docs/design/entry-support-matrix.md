# 入口安装/升级支持矩阵（entry support matrix）

入口 × 消费模式的安装与升级支持性清单。每个单元格声明受支持 / 不受支持 / 未认证,并指向可验证证据。未覆盖或未认证的组合是显式缺口,不默认当作受支持。本矩阵与[执行栈兼容矩阵](execution-stack-compatibility-matrix.md)互补:后者约束 wheel 版本窗口,本矩阵约束逐入口在该窗口内的功能覆盖。

## 消费模式

- **平台内置安装**:完整 Hecate 主应用部署(`uvicorn hecate.main:app`),入口随应用装配。
- **独立宿主**:`hecate-runner` 无控制面运行(step5b/5c 交付)。
- **受管宿主**:独立宿主 + 控制面注册/投影/租约(step4/6/7 交付)。

## 矩阵

| 入口 | 平台内置安装 | 独立宿主 | 受管宿主 | 证据 |
|---|---|---|---|---|
| HTTP chat（`/v1/chat/completions`） | 受支持 | 不适用（HTTP 入口属主应用面） | 不适用 | `tests/test_channel/`（G3 真实入口证据、step5d 契约收口用例）；CI `test` 作业 |
| Agents chat（`/v1/agents/{id}/...`） | 受支持 | 不适用 | 不适用 | 同上 |
| MCP `agent_chat` | 受支持 | 不适用 | 不适用 | `tests/test_mcp/test_agent_chat_entry_parity.py` |
| A2A（SendMessage/流式） | 受支持 | 不适用 | 不适用 | `tests/test_a2a/test_server/test_executor_entry.py` |
| IM 注入（飞书/Slack 路由） | 受支持 | 不适用 | 不适用 | `tests/test_core/test_im_providers.py`、`tests/test_gateway/test_im_routing.py` |
| 定时 agent | 受支持 | 未认证 | 未认证 | `tests/test_services/test_scheduling/test_executors.py::AgentExecutor` |
| 定时 workflow | 受支持 | 未认证 | 未认证 | `tests/test_services/test_scheduling/test_executors.py::WorkflowExecutor`（step5d 收口后经平台入口路径） |
| 评测批量执行 | 受支持 | 不适用 | 不适用 | `tests/test_evaluation/` |
| studio 测试运行（workflow test-run） | 受支持（测试语义,非生产入口） | 不适用 | 不适用 | `tests/test_workflow/test_test_runner.py` |

## 缺口与边界（如实登记）

- **独立/受管宿主**:宿主面当前交付只读执行与任务投递（SC01-SC05 场景覆盖）；IM/评测/studio 入口不属宿主面（不适用）。定时 agent/workflow 在宿主面的调度能力未交付、未认证——缺口登记于此,不预设交付步骤。
- **A2A per-caller 身份**:调用方→agent 的协议级映射仍是登记缺口（当前解析经 workspace 作用域 + 默认部署）;见 `a2a-protocol` 规格的过渡契约说明。
- **升级窗口**:所有入口的安装/升级均受[执行栈兼容矩阵](execution-stack-compatibility-matrix.md)的版本窗口约束;窗口外组合不受支持。
- **引擎路径默认开关**:聊天入口的引擎/直连路由默认值与翻转条件见 `unified-chat-execution` 规格与 step5d G3 默认启用复核文档;本矩阵不重复该决策。

## 管理方式

入口能力变化(新增入口、迁移路径、宿主面扩展)时同步更新本矩阵;单元格状态变化须附带证据变更(CI 作业或测试)。矩阵与执行栈兼容矩阵的版本窗口联动变更时,两份文档同一次变更内一起更新。
