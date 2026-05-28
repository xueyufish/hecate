## Why

P3 没有元智能体——系统维护依赖人工。P4 需要自动化系统维护的元智能体。

## What Changes

- **新增 `GarbageCollectorAgent`**：定期扫描过期资源（过期 session、孤立 checkpoint、未使用工具）
- **新增 `ComplianceCheckerAgent`**：定期扫描架构违规（代码风格、安全配置、依赖版本）
- **新增 `DriftDetectorAgent`**：检测配置漂移（实际配置与预期不一致）

## Capabilities

### New Capabilities

- `garbage-collector-agent`: 定期扫描和清理过期资源
- `compliance-checker-agent`: 定期扫描架构违规
- `drift-detector-agent`: 检测配置漂移

### Modified Capabilities

（无）

## Impact

- **新增代码**：`services/meta_agents/` 目录，约 400-600 行
- **新增依赖**：无
- **修改代码**：无（纯新增）
- **架构影响**：元智能体成为系统自我维护的机制
