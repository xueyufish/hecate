# Proposal

## Why

step2 已由 #195 合入并归档，但复核发现增量严格校验缺少基线、研究晋级可绕过证据门禁、受管表漂移仅报告首行、里程碑表列错位，且方案 §六多行未迁入功能处置数据。需要在 step3 开始前补齐规划与机器校验，避免完成勾选掩盖实际缺口。

## What Changes

- 增加 `check --base-ref`：按 Git 基线比较条目，严格校验新增/修改项，保留未变历史欠账；CI 在 PR、push、merge_group 使用对应基线并拒绝基线读取失败。
- 研究条目转为 delivered 时核对原状态和 evidence/acceptance，不能以同步添加 catalog ✅ 绕过；空字符串与空集合按缺失处理。
- 逐字段报告全部受管表漂移，校验表结构与必需区域，防止删除标记绕过治理；修复里程碑列标题。
- 补齐方案 §六处置映射与十类已实现能力决策记录，显式区分实现现状、目标边界、未验证使用量及迁移/回退；保留 Feature ID 和交付历史，新增独立缺口使用未占用后缀。
- 契约登记明确契约 owner、发布单元、状态 owner 和未确定的版本/窗口；修正定位/架构旧口径、依赖箭头和断连撤权条件，路线图将历史统计与未来顺序明确区分。
- 保存本次复核证据并更新方案 step2 的修正引用；不修改产品执行行为。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `feature-inventory-governance`: 明确基于可信 Git 基线的增量准入、研究晋级证据门禁、逐字段漂移及必需受管区域完整性，增加处置映射与契约登记结构验证。

## Impact

修改 `scripts/feature_inventory.py`、受管表数据、对应工具测试与 CI，调整定位/架构/ADR、catalog/roadmap/inventory 和方案完成记录；新增复核报告。沿用 schema 2 的扩展字段规则，不修改产品源码、数据库或对外 API，不授予生产支持。用户已明确授权本次 propose/apply；推送和归档另遵循仓库规则。
