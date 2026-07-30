## ADDED Requirements — 新增的需求

### Requirement: Sequential Pipeline template in catalog — 需求：目录中的顺序管道模板
系统应在模板目录中包含一个"Sequential Pipeline"编排模板，从 `data/orchestration_templates/sequential-pipeline.json` 自动发现。

#### Scenario: Sequential template listed in API — 场景：在 API 中列出顺序模板
- **WHEN — 当** 调用 `/api/orchestration-templates` 端点
- **THEN — 则** 响应应包含 `id` 为 "sequential-pipeline" 的条目，包含名称、描述、类别和预览元数据

### Requirement: Broadcast Pipeline template in catalog — 需求：目录中的广播管道模板
系统应在模板目录中包含一个"Broadcast Pipeline"编排模板，从 `data/orchestration_templates/broadcast-pipeline.json` 自动发现。

#### Scenario: Broadcast template listed in API — 场景：在 API 中列出广播模板
- **WHEN — 当** 调用 `/api/orchestration-templates` 端点
- **THEN — 则** 响应应包含 `id` 为 "broadcast-pipeline" 的条目，包含名称、描述、类别和预览元数据

### Requirement: Factory functions exported from templates module — 需求：从 templates 模块导出的工厂函数
系统应从 `engine/templates.py` 导出 `build_sequential_pipeline` 和 `build_broadcast_pipeline`。

#### Scenario: Import sequential pipeline factory — 场景：导入顺序管道工厂
- **WHEN — 当** 执行 `from hecate.engine.templates import build_sequential_pipeline`
- **THEN — 则** 导入应成功且函数可调用

#### Scenario: Import broadcast pipeline factory — 场景：导入广播管道工厂
- **WHEN — 当** 执行 `from hecate.engine.templates import build_broadcast_pipeline`
- **THEN — 则** 导入应成功且函数可调用
