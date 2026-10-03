# Proposal

## Why

step5 五个交付（`runtime-shared-assembly`、`runtime-standalone-distribution`、`hecate-runner-preview`、`platform-entry-migration`、`entry-tail-migration`，PR #206–#210）已合入。复核发现三处实现与规格/方案的实质偏差和一处门禁覆盖缺口：入口侧事件存储在进程内存在三个互不共享的单例实例（默认 `memory` 后端下跨入口事件互不可见，违反 platform-entry-execution 规格"装配含事件存储在入口间一致"）；A2A executor 以全局第一个非删除 agent 执行（无 workspace 过滤，多租户边界风险，严重性高于方案注记的"per-agent 身份缺口"表述）；A2A 失败响应把异常文本原样回传协议客户端；G3 门禁要求的"断线/恢复"真实入口测试缺失。另需为 5d 已登记的剩余项（调度器接线、studio 测试入口、A2A 身份）建立显式处置映射，防止后续 step 误判 5d 已整体完成。

## What Changes

- 入口侧共享装配收敛：在 `core/composition` 提供唯一的共享事件存储访问器（app lifespan 实例注册、入口只读消费）与公开的工具注册/加载装配函数；chat、MCP、IM、A2A、定时任务五个入口全部改用，删除 `im_entry`/`mcp server` 两处私有单例与四处复制的装配块；分层测试禁止入口模块自带存储单例或导入其他入口的私有装配函数。
- A2A executor 过渡期收紧 agent 选择：MUST 命中显式配置的 workspace（无配置时拒绝执行并返回协议内失败状态，不落全局第一个），异常响应不回传内部错误文本；per-agent 身份仍按登记缺口处理，由 `a2a-protocol` 规格记录过渡语义与退出条件。
- 补 G3 断线/恢复真实入口测试：经真实 HTTP 入口的两段式会话（第二段以同一 session 恢复执行，核对事件/状态连续与 tool 配对）；若实现存在阻塞则将阻塞证据与最小修复一并交付，不留未注记的空白。
- 产出 step5 复核报告（`docs/refactor/step5-review-report.md`）：逐项发现、证据、修正与处置映射；方案 step5 清单的 5d 保持未勾选并补处置注记，剩余项按 step6/后续条目显式归属。
- 同步 `platform-entry-execution` 规格：钉死"单进程一个共享事件存储实例"不变量与入口装配公开函数的唯一来源；`a2a-protocol` 规格补充过渡期 agent 解析语义。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `platform-entry-execution`: 装配一致性从"同工厂/同配置"收紧为"单进程单一共享实例 + 公开装配函数唯一来源"；分层约束扩展到入口自带单例与跨入口私有导入。
- `a2a-protocol`: 补过渡期 agent 解析语义（显式 workspace 配置、拒绝执行替代全局选取）与 per-agent 身份缺口的退出条件。

## Impact

涉及 `src/hecate/core/composition/`（新共享装配函数、wiring 注册）、五个入口模块（chat、MCP server、IM、A2A executor、定时任务 executor）的装配调用点改造、`src/hecate/channel/a2a/server/executor.py` 的 agent 解析与错误响应、分层测试与 G3/入口回归测试、`docs/refactor/step5-review-report.md` 与演进方案注记。行为变化：A2A 在未配置 workspace 时从"执行全局第一个 agent"变为"协议内失败"；IM/MCP/A2A/定时的引擎事件写入统一实例（memory 后端下跨入口可见性修复）。不实现 step6 调度接线、不做 per-agent 身份完整方案、不改变 hecate-runtime/hecate-runner 包边界。
