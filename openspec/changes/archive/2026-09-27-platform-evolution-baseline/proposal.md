# Proposal

## Why

`enterprise-agent-platform-evolution-plan.md`(下称"方案文档")第二节是作者一次性的人工快照判断("已合并/部分完成/待实现"),不承担实时状态源职责。step1 要求在动任何代码之前固定已知代码状态:后续 G1/G2 修复、step5 入口迁移、step2 能力处置、scenario-pack 实现,都需要一个可核验、带代码引用的事实基线作为对比基准——"修好了"还是"本来就坏了"、迁移触及哪些调用链,都以它为仲裁依据。方案文档明确交付物为 `docs/research/platform-evolution-baseline.md`。

## What Changes

- 新增 `docs/research/platform-evolution-baseline.md`,固定基线提交 `b8fd926`(feat/platform-evolution-baseline 分支),含 10 节:
  1. 基线声明(分支/提交/日期/在途状态/工作流偏离记录)
  2. 执行入口清单(8 类入口,归并 `main.py` 约 60 个 router 与 MCP/A2A/channel 入口,逐类记录执行服务、安全入口、事件存储、取消路径与缺口)
  3. 后端能力清单(Runtime/Memory/Knowledge/Evaluation/Observability/Sandbox,标注契约 spec 引用与内置实现状态)
  4. 信任与数据流拓扑(按部署形态,列出可能绕过授权或审计的直连路径)
  5. 托管执行组合数据流(harness/环境双轴登记格式,数据驻留/保留限制记录方式)
  6. 七能力域代码/数据库归属清单(文件与表 owner、跨包 import、直接写表违规)
  7. G1—G5 门槛记录(缺口、复现指针、owner 占位、目标 change)
  8. P01—P08 到平台保证的映射(含验收 fixture 占位与未支持能力标注)
  9. 架构评测包规格(fixture 目录结构 + 每场景断言清单;规格冻结,实现归 `platform-evolution-scenario-pack`)
  10. 旧分支归档(核实结果: `origin/docs/platform-evolution-plan` 与 main 无差异)
- 纯文档 change:不改任何业务代码,不新增测试 fixture,不修改 `feature-inventory.yaml`(那是 step2/G5 的范围)
- 写作纪律:只写已验证事实与缺口,每条结论带 `file:line` 或测试证据;前瞻性表述一律指向方案 step,不在基线中重新规划

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

(无——本 change 交付研究文档,无系统行为变化,`.openspec.yaml` 已设 `skip_specs: true`)

## Impact

- 仅新增一个 Markdown 文档,无代码、API、依赖、数据库影响
- 直接消费方:G1/G2 修复 change(复现指针与对比基准)、`platform-evolution-scenario-pack`(评测包规格)、step2(能力处置证据)、step5(入口迁移 checklist)
- 需遵守 `docs/design/writing-style.md` 的写作约束
