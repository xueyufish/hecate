# Proposal

## Why

用户提出车商 App 私有化交付这一具名消费者，要求执行组件在没有 Hecate 管理服务时也能独立使用。原演进方案默认控制面拥有全部任务状态，并将 Runtime 独立打包留到 step19，不能充分指导这一目标的实施。

## What Changes

- 修订研究方案的交付边界、部署模式、状态所有权及离线授权规则。
- 增补 step1 基线，不重做已完成项目；在 step5 提前交付独立执行宿主与发行包。
- 同步 step3—step19 的依赖、发布与治理规则、功能目录映射、迭代切片和验收矩阵。
- 区分可独立运行的技术预览与可处理生产写入的支持 profile，给出明确实施顺序。

## Capabilities

无运行行为或正式产品 spec 变更。本 change 仅调整研究计划，使用 `skip_specs: true`；后续实现分别建立对应的 proposal/design/specs/tasks，不以本 change 替代能力规格。

## Impact

- 修改 `docs/research/enterprise-agent-platform-evolution-plan.md`，并按归档约定同步 `docs/design/positioning.md` 的定位说明和 P1—P5 目录权威来源；不修改历史基线快照、源码、功能清单或产品路线图。
- 本次授权为制定并保存计划，不启动后续 Runtime 重构、不提交或推送。
- 复用当前已附加的干净 worktree，在文档分支工作；不重复创建 worktree，不在 main 编辑。
- 验证文档结构、步骤依赖、历史勾选项、相互引用和 OpenSpec 文档变更；不以源码测试替代文档核验。
