# Proposal

## Why

Step4 已合并实体和登记服务，但若干完成证据只覆盖正常路径，版本归属、身份绑定、会话实体、准入和迁移保留与演进方案不一致。修正这些基础边界，避免后续执行组件建立在错误数据上。

## What Changes

- 校验 principal 的 workspace/组织/负责人归属，审计使用真实组织。
- 校验版本归属和完整能力快照；登记不允许自行提升 enforced 等级，托管配置保留事实来源与核验时间。
- Run 核对部署身份，固化受众和执行配置；并发尝试号在 Task 上串行分配。
- 会话绑定真实 conversation，并提供存量 session 的显式解析；后端会话绑定不可覆盖或因软删除复用；历史导入按来源幂等。
- 独立宿主登记保持 pending；准入必须经可信解析器和 workspace 管理员，拒绝不得启用受管新任务。
- 增量迁移修正数据库约束和已有映射，历史核验不足的数据保持待治理；阻止破坏性 downgrade。
- 更新 Step4 证据和审查报告，明确 Step6/7 的真实运行路径仍未交付。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `agent-deployment`: 租户归属、能力登记和审计边界。
- `task-run`: 固化身份/配置、真实会话映射、幂等导入和可信准入。

## Impact

涉及 execution 登记服务、execution identity 契约、Step4 ORM/迁移、定向回归测试与演进方案。服务参数收紧，未接入可信宿主解析器时不能授予受管准入。不修改运行时执行入口，不增加依赖，不实现 Step6/7 调度和授权。
