# Design

## Context

`feat/platform-evolution-baseline` 分支(基点 `b8fd926`),工作区无在途 change。交付目标为单一文档 `docs/research/platform-evolution-baseline.md`;调研输入包括 `src/hecate/main.py` 的约 60 个 router 挂载与 3 组延迟挂载、`openspec/specs/` 下 100+ 既有 spec、方案文档 §二/§六的门槛与边界复核结论、`tests/` 下既有测试证据。动机见 proposal.md — Why,不赘述。

## Goals / Non-Goals

**Goals:**

- 每条结论可核验:带 `file:line`、测试文件名或 spec 引用;查不到证据的写"未核验"并留 TODO,不以推测填表
- 作为冻结快照:声明基线提交,后续 change 各自记录增量,基线不跟随更新
- 评测包规格在本 change 内冻结到"scenario-pack 可直接实现"的深度(切法 A)

**Non-Goals:**

- 不实现任何 fixture、不改任何业务代码、不动 `feature-inventory.yaml` 与 feature catalog/roadmap(step2/G5 范围)
- 不做安全修复(G1/G2 是独立 change,本 change 只记录复现指针)
- 不复写方案文档的前瞻规划;基线中前瞻表述一律是指向 step 的指针

## Decisions

**D1 文档定位:可核验事实集,非状态面板。**
替代方案:(a) 直接引用方案 §二——被否,那是无引用粒度的人工判断;(b) 各后续 change 自行调研——被否,重复劳动且失去共同对比基准。写作自检标准:"这句话六个月后还有人来查吗?查的时候能顺着代码引用核验吗?"查不住的不写。

**D2 十节结构,逐节固定调查方法:**

| 节 | 内容 | 调查方法 |
|---|---|---|
| 1 基线声明 | 分支/提交/日期/在途状态/工作流偏离记录 | git 命令 + `openspec list` |
| 2 入口清单 | 8 类入口 ×(router、执行服务、安全入口、事件存储、取消路径、缺口) | bottom-up:以 `main.py` 的 `include_router` 全量清单 + MCP server、A2A、channel 包为起点,逐类追 router → 应用服务 → 执行引擎 → 事件存储 → 取消 |
| 3 后端清单 | 6 类后端 ×(契约是否存在、spec 引用、内置实现、缺口) | 引用 `openspec/specs/` 既有 spec 作为契约证据,不凭印象写 |
| 4 信任拓扑 | 按部署形态的数据流 + 可绕过授权/审计的直连路径清单 | 沿入口清单的调用链标注安全边界 |
| 5 托管组合数据流 | harness/环境双轴登记格式;驻留/保留限制的记录方式 | 以方案 §一的 OpenAI 事实为登记格式样例 |
| 6 归属清单 | 七能力域的文件/表 owner、跨包 import、直接写表 | grep 违规 import 与跨域表写入,逐条列文件 |
| 7 G1—G5 门槛 | 缺口、复现指针、owner、目标 change | 指向方案 §二验证范围与具体测试文件,复验关键断言 |
| 8 P01—P08 映射 | 平台保证、可选实现、责任 step、fixture 占位、未支持项 | 从方案 §一映射表细化,fixture 列留占位 |
| 9 评测包规格 | fixture 目录结构 + 每场景断言清单 | 按 D3 冻结 |
| 10 旧分支归档 | 核实结果与指针 | 已核实:`origin/docs/platform-evolution-plan` 与 main 无差异,记录之 |

**D3 评测包规格采用切法 A(规格冻结在基线,实现归 scenario-pack)。**
切法 B(基线只列场景类别)被否:方案要求 step1 固定评测包,它是后续所有 step 的对比基准,规格后置会让 step1 的该项只能算部分完成,且两个 change 互相等待。代价:第 9 节深约一两百行,形同小型设计文档,可接受。规格必须包含:合成输入材料及 ACL 表达、测试工具集、预期产物 schema、禁止动作清单、审批人设定、故障注入点与环境复位、确定性断言与 rubric 的分工(内容质量用明确 rubric + evaluator 版本,不用"Agent 自称完成")。

**D4 owner 字段留占位。** G3/G4/G5 的 owner 记 `待指定`,方案 §七规定人员由用户安排,不虚构;不阻塞文档合并。

**D5 记录工作流偏离。** 基线声明一节注明本 change 以主目录分支(而非 opsx-flow worktree)承载,系用户决策,避免后来者据文档约定误判分支来源。

**D6 语言与风格。** 中文正文、英文技术词,与方案文档一致;基线声明中的提交哈希与日期属事实记录,不属 `docs/design/writing-style.md` 限制的"描述性标记",仍通读该规则确认边界。

## Risks / Trade-offs

- [文档膨胀、与方案漂移] → D1 自检标准 + 前瞻只留指针;评审时对每节问"删了会怎样"
- [代码引用随后续提交失效] → 快照语义使然:引用失效即"已过时"的信号,不是缺陷
- [入口归并遗漏] → 以 `include_router` 全量清单为 bottom-up 起点,不以记忆归类;MCP/A2A/channel 包入口单独核对
- [规格与 scenario-pack 实现脱节] → 规格按节编号;scenario-pack 的 proposal 必须引用章节号,改规格须回到基线并注明
- [调查量超预期(约 60 个 router)] → 允许个别 router 标"未核验"并留 TODO,不以猜测填表;TODO 集中在第 2 节末尾的缺口清单

## Migration Plan

纯新增文档,无部署动作。回滚 = 删除该文件;分支回退不涉及数据或行为。

## Open Questions

无阻塞性问题。G3/G4/G5 owner 由用户随时指定后补入(见 D4)。
