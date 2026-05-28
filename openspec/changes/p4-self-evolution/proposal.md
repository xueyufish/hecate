## Why

P3 的 Harness 优化闭环只能生成约束规则，不能自动改进 Agent 本身。P4 需要自我进化能力——从失败中学习，自动改进策略。

## What Changes

- **新增 `TrajectoryAnalyzer`**：分析成功/失败轨迹，提取改进点
- **新增 `PolicyEvolver`**：基于轨迹分析自动调整 Agent 策略
- **新增 `SyntheticEnvironmentGenerator`**：生成合成训练环境（参考 TRACE）
- **新增 `OnPolicyTrainer`**：基于实际执行数据的在线训练（参考 FATE）

## Capabilities

### New Capabilities

- `trajectory-analysis`: 成功/失败轨迹分析
- `policy-evolution`: 基于分析自动调整策略
- `synthetic-environment`: 合成训练环境生成
- `on-policy-training`: 在线策略训练

### Modified Capabilities

（无）

## Impact

- **新增代码**：`services/evolution/` 目录，约 600-900 行
- **新增依赖**：无（复用 LLM 调用）
- **修改代码**：无（纯新增）
- **架构影响**：自我进化成为系统的高级能力
