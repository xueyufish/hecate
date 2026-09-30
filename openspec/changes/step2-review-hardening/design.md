# Design

## Context

见 proposal.md。复现基于 #195 / `32e6fdc`：非严格清单检查退出 0，但增量比较与研究晋级证据检查不存在；里程碑 header 四列、数据五列；漂移诊断仅显示首条不同文本。受管处置表未包含 `provider_or_adapter` 或真正的处置标签，契约表没有 owner，§六不少 ID 未登记。

## Goals / Non-Goals

**Goals:** 在保持历史交付状态和欠账的前提下，使新增规划可审查、CI 能阻止新增欠账和无证据晋级。

**Non-Goals:** 不一次补齐所有历史条目，不实现执行后端或独立宿主，不把规划文档视为产品认证。

## Decisions

### D1: 显式 Git 基线与独立全量模式

`check --base-ref REF` 从 Git 读取原清单，以整条目语义比较挑出新增/修改项；这些项缺少治理、evidence、acceptance 则失败，未变历史项只报告欠账。`--strict` 无基线保留全量严格语义。基线不可解析、文件缺失或 schema 异常时失败，避免退回宽松校验。CI checkout 完整历史，PR 取 base.sha，push 取 before，merge_group 取 base_sha；首次 push 无基线用全量严格模式。相比维护手填 grandfather 白名单，Git 基线不会掩盖后续变更。

### D2: 晋级检查使用原状态

基线中 research-candidate、或当前保留研究生命周期字段的条目转 delivered，必须有 evidence/acceptance；catalog 的 ✅ 仅检查一致性。空字符串/集合不算填写，production 的 n/a/pending 不构成证据。研究生命周期所有字段逐项报告缺失，允许显式 pending，不伪造具名负责人。

### D3: 治理表结构与完整性

对声明的受管表校验 header/分隔行/数据列数及注册数据结构，逐 ID+字段输出所有差异；行增删、正文与 marker 差异同样显式报告。正式文档必须包含预期区域且保持 strict=true，删除区域不能跳过检查。sync 改写前验证所有文档，保留外部正文。单测的临时 demo 区域不受正式文档完整性清单约束。

### D4: 处置与实现现状独立

schema 2 使用可选扩展字段 `disposition` 表达 core-contract / builtin-reference / optional-component / integration / retire-candidate。§六全部映射登记于受管数据；已交付实现的 implementation_mode/provider 保持当前实际形态，目标边界与退出步骤另列，未知使用量明确未验证。仅本次映射修改的条目补 planning evidence、未来 acceptance 与 maturity=unverified（不是批量提升生产级别）；保留已有证据/验收，新增解释注明证据范围。

### D5: 契约与候选决策记录

契约 owner 按领域责任包登记，人类实现/验收负责人在所属 change 启动时指定。十类已实现候选引用独立基线实际包/挂载清单，记录迁移和回退窗口；不存在使用量或生产测试证据时不决定退役。定位/架构与 ADR 固定运行调用方向和源码依赖方向，托管断连的撤销陈旧窗口成文。未来路线图以迭代/里程碑为准，旧统计标历史且不冒充当前数量。

## Risks / Trade-offs

- 新增/变更条目可能只具有规划证据 → acceptance 写未来行为，maturity 明确未认证，报告解释证据不是运行通过。
- Git 基线导致本地全量严格仍失败 → 历史欠账保留；本次与 CI 使用可信 base-ref，输出增量范围。
- 大量历史 catalog 叙事含旧假设 → §六逐行决策覆盖并优先修正显式矛盾，保留历史交付说明。

## Migration Plan

工具、数据、受管文档和 CI 同步交付；先完成基线/晋级与漂移保护，再补映射和文档，最后运行 scoped tests 与既定检查。回退为该修正 PR 的 revert，不删除执行记录；合并后由用户触发归档。
