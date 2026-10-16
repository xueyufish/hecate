# Proposal

## Why

演进方案将工单示例写成统一验证流程，容易让实施者误认为平台必须内置对应业务能力，或每个任务都必须经过审批及结构化输出。本次按用户要求调整方案，明确业务无关的平台责任与可替换测试 fixture 的边界。

## What Changes

- 将工单相关的总体验证流程与团队验收改为工具反馈、授权、产物校验和可靠交接等通用能力要求。
- 明确审批由策略决定、输出 Schema 由任务声明；持久等待与进程重启作为指定恢复场景验收，不规定所有任务的固定流程。
- 明确具体业务规则由调用方或业务系统提供，合成业务工具仅作为可替换测试 fixture；既有测试名称与历史证据保留。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

无。本次仅修订规划文档，不改变现有执行、审批及输出契约；设置 `skip_specs: true`，不创建 delta spec。

## Impact

仅修改 `docs/refactor/enterprise-agent-platform-evolution-plan.md` 并记录本 change。运行代码、公共 API、测试工具、历史记录、步骤编号及交付状态不变；不启动下一项功能开发。
