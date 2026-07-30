## Why — 为什么

引擎支持顺序管道和广播模式的所有原语（TOPIC 通道、顺序边解析、FAN_OUT/MERGE、AgentWorker），但开发人员必须为每个节点手动构造具有正确通道接线的 Graph DSL 字典。两种常见的多智能体模式——线性顺序管道（CrewAI `Process.sequential`、AgentScope `sequential_pipeline`）和共享通道广播（AgentScope `MsgHub`、AutoGen `RoundRobinGroupChat`）——缺乏一流的工厂函数，迫使用户理解底层通道语义来表达简单的多步骤工作流。

存在一个 `content-pipeline.json` 模板用于 researcher→writer→reviewer 模式，但它是一个单一的硬编码用例。引擎需要通用的、参数化的工厂函数，接受阶段（或参与者）列表并自动产生正确接线的 Graph DSL。

## What Changes — 变更内容

- 向 `engine/templates.py` 添加 `build_sequential_pipeline()` 工厂函数——接受阶段定义列表并生成线性 A→B→C→... 图，带自动接线的 TOPIC + LAST_VALUE 通道、可选的修订循环和可选的质量门。
- 向 `engine/templates.py` 添加 `build_broadcast_pipeline()` 工厂函数——接受参与者定义列表并生成顺序轮询图，所有参与者共享同一个 TOPIC 通道，带可选的轮次限制和终止条件。
- 添加 JSON 模板：`sequential-pipeline.json` 和 `broadcast-pipeline.json` 到 `data/orchestration_templates/`。
- 用新的模板元数据更新 `orchestration-templates` API 响应。

## Capabilities — 能力

### New Capabilities — 新能力
- `sequential-pipeline`：用于确定性多步骤顺序管道的工厂函数和模板，带自动接线通道、可选的修订循环和阶段间数据流
- `broadcast-pipeline`：用于共享通道广播模式的工厂函数和模板，带顺序轮询执行和共享消息可见性

### Modified Capabilities — 修改的能力
- `orchestration-templates`：将顺序管道和广播管道添加到模板目录；更新列表端点元数据

## Impact — 影响

**引擎层**（`src/hecate/engine/`）：
- `templates.py`——2 个新的工厂函数（各约 120 行）
- 无需更改 types.py、compiler.py、pregel.py 或 graph_dsl.py——所有原语已存在

**数据**（`src/hecate/data/orchestration_templates/`）：
- 2 个新的 JSON 模板文件

**测试**（`tests/`）：
- 两个工厂函数的新测试用例（Graph DSL 结构验证、通道接线验证）
- 无需更改集成测试（仅引擎层）

**API/服务**：无需代码变更——现有的 orchestration-templates API 从数据目录自动发现新的 JSON 文件。

**破坏性变更**：无——纯新增。
