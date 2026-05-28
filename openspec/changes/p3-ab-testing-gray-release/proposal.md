## Why

P1 的模型路由只有简单 fallback 列表，无法做 A/B 测试、无法灰度发布、无法按规则路由。P3 需要完整的模型路由引擎。

## What Changes

- **新增 `ModelRouter`**：按规则路由到不同模型（成本/延迟/能力/随机）
- **新增 `ABTestManager`**：流量分割 + 指标收集 + 效果对比
- **新增 `GrayReleaseManager`**：加权路由灰度发布（如 70/30 分流）
- **扩展 `LLMService`**：集成路由引擎，替代简单 fallback

## Capabilities

### New Capabilities

- `model-routing`: 按规则路由（成本/延迟/能力/随机）
- `ab-testing`: 流量分割 + 指标收集 + 效果对比
- `gray-release`: 加权路由灰度发布

### Modified Capabilities

（无）

## Impact

- **新增代码**：`services/llm/routing.py`、`services/llm/ab_testing.py`，约 500-700 行
- **修改代码**：`services/llm/service.py`（集成路由引擎）
- **新增依赖**：无
- **架构影响**：LLMService 变为可插拔路由，支持多种策略
