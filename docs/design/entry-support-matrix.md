# 入口安装/升级支持矩阵（entry support matrix）

本矩阵记录入口 × 消费模式的安装与升级认证。功能回归通过只能证明测试环境中的执行行为，不能替代干净安装或从支持版本升级后的逐入口验证。[执行栈兼容矩阵](execution-stack-compatibility-matrix.md)约束包版本窗口，不自动授予入口认证。

## 消费模式

- **平台内置安装**:完整 Hecate 主应用部署(`uvicorn hecate.main:app`),入口随应用装配。
- **独立宿主**:`hecate-runner` 无控制面运行(step5b/5c 交付)。
- **受管宿主**:独立宿主 + 控制面注册/投影/租约(step4/6/7 交付)。

## 矩阵

| 入口 | 平台内置安装/升级 | 独立宿主 | 受管宿主 | 已有功能证据与认证缺口 |
|---|---|---|---|---|
| HTTP chat（`/v1/chat/completions`） | 未认证 | 不适用 | 不适用 | `tests/test_channel/`；真实协议入口及确定性 provider，缺安装/升级后入口证据 |
| Agents chat（`/v1/agents/{id}/...`） | 未认证 | 不适用 | 不适用 | `tests/test_channel/test_chat_engine_g3_entry.py`；包含停止关联、输出校验投影及流式信封回归，缺安装/升级认证 |
| MCP `agent_chat` | 未认证 | 不适用 | 不适用 | `tests/test_mcp/test_agent_chat_entry_parity.py`；不等于 G3 多轮/恢复和安装/升级认证 |
| A2A（SendMessage/流式） | 未认证 | 不适用 | 不适用 | `tests/test_a2a/test_server/test_executor_entry.py`；执行器回归不等于完整协议传输或安装/升级认证 |
| IM 注入（飞书/Slack） | 未认证 | 不适用 | 不适用 | `tests/test_core/test_im_providers.py`、`tests/test_gateway/test_im_routing.py`；缺实际安装/升级与终态端到端认证 |
| 定时 agent | 未认证 | 未认证 | 未认证 | `tests/test_services/test_scheduling/test_executors.py::TestAgentExecutor`；缺安装/升级认证，宿主调度未交付 |
| 定时 workflow | 未认证 | 未认证 | 未认证 | `tests/test_services/test_scheduling/test_executors.py::test_real_scheduled_workflow_preserves_business_channels`；真实图输入/分支回归，缺 Run 身份、治理及安装/升级认证 |
| 评测批量执行 | 未认证 | 不适用 | 不适用 | `tests/test_execution/test_entry_service.py`、`tests/test_services/test_evaluation/`；缺终态与安装/升级端到端认证 |
| studio workflow test-run | 未认证 | 不适用 | 不适用 | `tests/test_services/test_workflow/test_test_runner.py`；测试端点语义，缺安装/升级认证 |

## 缺口与边界（如实登记）

- **独立/受管宿主**:宿主面当前交付只读执行与任务投递（SC01-SC05 场景覆盖）；IM/评测/studio 入口不属宿主面（不适用）。定时 agent/workflow 在宿主面的调度能力未交付、未认证——缺口登记于此,不预设交付步骤。
- **A2A per-caller 身份**:调用方→agent 的协议级映射仍是登记缺口（当前解析经 workspace 作用域 + 默认部署）;见 `a2a-protocol` 规格的过渡契约说明。
- **定时 workflow 的 Run 身份**：当前没有 AgentDeployment，只能登记 Task；返回真实 Task 引用和 `missing` 标记，不宣称完整 Task/Run 关联或治理认证。后续需要独立 workflow 的部署身份模型；本轮不伪造身份或要求已有配置新增 Agent。
- **A2A 默认语义**：default deployment 是每个 Agent 的默认部署。多 Agent workspace 恰好一个 Agent 持有 live default deployment 时可解析；多个 Agent 各有默认部署时仍拒绝。
- **升级窗口**:所有入口的安装/升级均受[执行栈兼容矩阵](execution-stack-compatibility-matrix.md)的版本窗口约束;窗口外组合不受支持。
- **引擎路径默认开关**:聊天入口的引擎/直连路由默认值与翻转条件见 `unified-chat-execution` 规格与 step5d G3 默认启用复核文档;本矩阵不重复该决策。

## 管理方式

入口能力变化时同步更新本矩阵；声明安装/升级受支持必须链接安装/升级后实际入口验证证据。版本窗口变化时，两份矩阵同步更新。
