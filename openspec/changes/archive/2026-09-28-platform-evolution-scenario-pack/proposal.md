# Proposal

## Why

演进方案(`docs/research/enterprise-agent-platform-evolution-plan.md`)step1 要求固定实施基线与验证场景:后续所有 step 的对比都必须落在同一个可重复的评测包上。当前仓库缺少这个落地物——权限负例、副作用恢复负例、入口级行为回归、成本/结果基线分散在各处测试里,没有统一的场景标识、没有与 P01—P08 能力映射的可追溯清单,也没有保留旧路径响应协议与事件样本作为迁移比较基准。I-A 迭代的退出条件(固定架构评测包和责任映射)依赖本 change。

本 change 是 step1 四个 change 中的最后一个:`g1-mcp-action-enforcement` 与 `g2-tool-receipt-recovery` 落地后,其对应负例才能全绿;语料、manifest、REST 路径场景与黄金样本不依赖这两个修复,可先行实现。

## What Changes

- 新增 `tests/scenarios/` 场景包,包含五类资产:
  - `manifest.yaml`:场景 ID ↔ P01—P08 问题组 ↔ 断言 ↔ 未支持能力声明的唯一机器可读事实源;`docs/research/platform-evolution-baseline.md`(独立 change)引用它,不复制它。
  - 合成语料:少量带版本、权限(ACL)和标准引用位置的文档,配合语料清单;不把工作簿的固定样本规模变成平台要求。
  - stub 企业工具:确定性测试工单服务等,记录调用与副作用,不接入真实外部系统。
  - Tier 1(CI 确定性层,pytest):五个可重复场景(正常执行、拒绝动作、等待审批、后端失联、重复提交)走真实 HTTP 入口;权限负例(跨租户、viewer 写、未批准写);副作用恢复负例(结果缺失不盲目重试);旧路径黄金样本(响应协议结构与事件类型序列,不逐字比较模型文本)。
  - Tier 2(记录基线层,opt-in,不进 CI):单 Agent 成本/结果基线与内容质量 rubric 跑批,复用现有 evaluation 模块的数据集/评估器格式并记录 evaluator 版本。
- P 组覆盖切薄:P05/P06/P07 由五个可重复场景覆盖核心;P01/P03 以最小合成语料贯通;P02 仅落语料版本 fixture;P04 显式标记 deferred;P08 由事件样本部分覆盖。未支持能力在 manifest 中显式声明,不以默认通过掩盖。
- 不修改产品源代码行为;本 change 交付测试资产与记录数据。

## Capabilities

### New Capabilities

- `platform-scenario-pack`:场景包的组成与治理——manifest 一致性要求、Tier 1/Tier 2 划分规则、P 组覆盖与未支持能力声明、黄金样本的结构断言规则、场景与断言的可追溯性。

### Modified Capabilities

(无——纯新增测试资产;Tier 2 复用 evaluation 模块既有数据集格式,不修改其需求。)

## Impact

- **新增代码/数据**:`tests/scenarios/`(manifest、语料、stub 工具、场景测试、baselines、goldens);无生产源码改动。
- **CI**:新增确定性测试随 pytest 运行,不引入外部服务依赖或真实模型调用。
- **前置依赖**:MCP 路径权限负例以 `g1-mcp-action-enforcement` 为前置;副作用恢复负例与重复提交语义以 `g2-tool-receipt-recovery` 为前置。两个修复未合并前,对应任务保持未完成,不得以 skip 掩盖。
- **文档关联**:`docs/research/platform-evolution-baseline.md` 的 P01—P08 映射表与执行入口清单引用本包 manifest。
- **不受影响**:API 契约、数据库 schema、现有 spec 行为。
