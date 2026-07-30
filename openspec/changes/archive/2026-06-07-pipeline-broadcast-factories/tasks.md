## 1. 顺序管道工厂

- [x] 1.1 在 `src/hecate/engine/templates.py` 中实现 `build_sequential_pipeline()`——接受 `stages: list[dict[str, str]]`、`revision_config: dict | None = None`；验证最少 2 个阶段，拒绝重复 ID；构建带自动接线通道（共享 `messages` TOPIC + 每阶段 `{stage_id}_output` LAST_VALUE）的 AGENT 节点；构建线性边；返回 GraphConfig
- [x] 1.2 向 `build_sequential_pipeline()` 添加修订循环支持——当提供 `revision_config` 时，在最后阶段后追加 CONDITION 节点，条件边路由到 `target_stage`（条件为真时）和 `__end__`（条件为假时）；添加 CONDITION 节点可读的 `revision_status` LAST_VALUE 通道

## 2. 广播管道工厂

- [x] 2.1 在 `src/hecate/engine/templates.py` 中实现 `build_broadcast_pipeline()`——接受 `participants: list[dict[str, str]]`、`moderator: dict | None = None`；验证最少 2 个参与者，拒绝重复 ID；构建所有共享同一个 `messages` TOPIC 通道（可读+可写）的 AGENT 节点；构建顺序轮询边；返回 GraphConfig
- [x] 2.2 向 `build_broadcast_pipeline()` 添加主持人支持——当提供 `moderator` 时，在开始和结束处都插入主持人 AGENT 节点：`__start__` → moderator → participant_0 → ... → participant_{N-1} → moderator → `__end__`

## 3. JSON 模板

- [x] 3.1 创建 `src/hecate/data/orchestration_templates/sequential-pipeline.json`——3 阶段 researcher→writer→reviewer 管道带修订循环，遵循 `content-pipeline.json` 结构并具有正确的通道接线
- [x] 3.2 创建 `src/hecate/data/orchestration_templates/broadcast-pipeline.json`——3 参与者轮询广播带主持人，演示共享 `messages` TOPIC 通道模式

## 4. 测试

- [x] 4.1 在 `tests/test_engine/test_pipeline_broadcast_templates.py` 中为 `build_sequential_pipeline()` 添加测试——测试基本 2 阶段管道结构（节点、通道、边），测试 3 阶段阶段间通道接线（可读/可写），测试修订循环（CONDITION 节点、条件边），测试验证错误（< 2 阶段、重复 ID）
- [x] 4.2 在 `tests/test_engine/test_pipeline_broadcast_templates.py` 中为 `build_broadcast_pipeline()` 添加测试——测试基本 3 参与者结构（节点、共享通道、边），测试主持人模式（主持人在开始和结束处），测试验证错误（< 2 参与者、重复 ID）
- [x] 4.3 为 JSON 模板添加测试——验证 `sequential-pipeline.json` 和 `broadcast-pipeline.json` 通过 `parse_graph()` 加载和解析正确、无错误编译，并包含预期的节点/通道/边结构

## 5. 验证

- [x] 5.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`——全部通过
