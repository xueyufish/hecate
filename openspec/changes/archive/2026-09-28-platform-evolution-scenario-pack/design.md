# Design

## Context

见 proposal.md — Why。补充与实现相关的现状:

- 评测模块(`src/hecate/ops/evaluation/`)已有 dataset 版本化、`Score.source`(deterministic/llm_judge/human)、`AnswerSource`(含 AGENT/WORKFLOW)、publish_gate——Tier 2 可直接复用,不必新造声明式格式。
- `tests/test_e2e/` 已确立"真实 HTTP 入口 + 种子 EventStore + 确定性断言"的回归模式(conftest 提供 `client` fixture);审批链路已有 ASK 规则 → `REQUIRE_APPROVAL` → 审计事件对的端到端测试可参照。
- 测试约定(tests/AGENTS.md):in-memory SQLite、stub 类、无 factory、无 mock 框架;runtime 测试已有 `StubPort` 等假 LLM 惯例。
- `Problem_Lab.xlsx` 是用户自备输入,不在仓库内;场景包只内嵌从其提炼的映射(演进方案第一节),不内嵌工作簿。

## Goals / Non-Goals

**Goals:**

- 建立后续所有 step 共用的同一评测包:可重复、可追溯、失败可见。
- 权限/副作用行为有确定性负例;旧路径有结构级黄金样本。
- manifest 成为场景 ↔ P 组 ↔ 断言 ↔ 未支持能力的机器可读事实源。

**Non-Goals:**

- 不造场景 DSL 或通用 runner;不建平台 Task 级幂等 API(step6 交付)。
- 不做知识接入生命周期(step9c)、图检索(P04)、生产负载与符合性矩阵(step16)。
- 不接入真实模型、真实企业系统;不在 CI 做内容质量评分。
- 不修改产品源代码行为(允许为可注入性补充测试钩子,须在 tasks 中单列评审)。

## Decisions

### D1:pytest 原生场景 + manifest 追溯表,不建声明式 runner

场景就是 pytest 测试,manifest(`tests/scenarios/manifest.yaml`)只做追溯与覆盖声明,由一个 pytest 一致性测试在 CI 强制(manifest 中每个场景 ID 必须有对应测试收集项,P01—P08 覆盖完整,未支持标注合法)。

- 备选:场景 YAML + 通用 runner。放弃原因:当前只有一个消费者,为它建执行框架违反"第二个真实实现才抽象"的仓库约定;且权限/副作用断言需要代码级注入(故障点、stub 替换),YAML 表达不了。
- Tier 2 的声明式需求由 evaluation 模块既有数据集格式承担,不并行造第二套。

### D2:两层划分,Tier 1 封闭、Tier 2 出 CI

- Tier 1:五场景、权限负例、恢复负例、黄金样本、manifest 一致性测试。只依赖 stub 模型、in-memory SQLite、stub 工具;`pytest -m` 不需要新 marker,靠目录隔离(`tests/scenarios/` 下 Tier 2 脚本不放 `test_` 前缀文件,避免收集)。
- Tier 2:成本/结果基线与 rubric 跑批,输出 JSON 记录到 `tests/scenarios/baselines/`,附带录制命令说明;复用 evaluation 模块格式,记录 evaluator 版本。业务收益标注未验证。
- 备选:全部进 CI 用固定阈值。放弃原因:真实模型跑批不可重现且引入凭据依赖;纯 stub 的"成本"只对相对比较有意义,作为 Tier 2 记录而非门禁。

### D3:场景 ID 采用全局 `S<nn>`,manifest 多对多映射 P 组

一个场景常覆盖多个 P 组(如审批场景同时覆盖 P06/P07),多对多映射比按组编号更真实;ID 稳定后供基线文档与后续 step 引用。备选:`P05-01` 式按组编号,放弃原因:会诱导一一对应,掩盖交叉覆盖。

### D4:合成语料为 markdown + `corpus.yaml`

少量文档(个位数),每份带 `version`、`acl`(主体 → 读/写)、`citations`(标准引用位置:段落/表格单元格锚点)。检索/工具侧通过现有 Memory/RAG 测试基础设施加载;断言引用时定位到 corpus.yaml 声明的锚点。

### D5:黄金样本只断言结构

录制:stub 模型 + HTTP `client` 对旧路径(现有聊天与工作流执行入口)采样,存 `tests/scenarios/goldens/`(响应协议子集 + 事件类型序列,剔除文本载荷)。断言字段存在性、类型、事件顺序与状态语义;文本不比较。文件头记录录制时代码路径与用途,供 step5 迁移对比。

### D6:依赖前置的任务分批,不用 skip 掩盖

语料、manifest、REST 路径场景、黄金样本无前置,先行;MCP 权限负例等 `g1-mcp-action-enforcement` 合并,恢复/重复提交负例等 `g2-tool-receipt-recovery` 合并。tasks.md 对应任务保持未勾选,不以 `xfail`/`skip` 让 CI 假绿。

## Risks / Trade-offs

- [G1/G2 修复延期拖住本 change 收口] → 独立部分先行交付并可用;tasks 分批标注前置,不阻塞已可验收场景。
- [manifest 与测试漂移] → CI 一致性测试强制双向核对(有测试无条目、有条目无测试、覆盖缺失均失败)。
- [黄金样本脆断] → 只断言协议结构子集;文本字段显式剔除并在文件头说明。
- [异步时序引入 flaky] → 故障注入用确定性替换(stub 抛错、事件丢失),不依赖 sleep/竞态;复用现有 e2e 的注入模式。
- [Tier 2 基线被误当门禁] → 记录文件头部声明"记录,非门禁";CI 不收集。

## Migration Plan

纯新增测试资产,无产品迁移。实施顺序:D6 的分批顺序;回滚 = 整分支 revert,不影响任何运行时行为。

## Open Questions

- 五场景各入口的具体端点选择(聊天 REST vs 工作流执行 API 的分配)在实现时按现有 API 形状定,不影响结构。
- Tier 2 成本基线是否同时保留 stub-token 的 CI 内回归版本:默认不做(见 D2),若实施中发现相对比较确需门禁再议。
