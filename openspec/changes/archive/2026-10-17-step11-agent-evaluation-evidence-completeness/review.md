# Codex 复核 — step11-agent-evaluation-evidence-completeness

复核对象为 feat/step11-agent-evaluation-evidence-completeness 的未提交实现。下列发现均已直接修正，当前切片没有遗留阻断项。分支仍未提交、未推送；PR review 与 CI 留在后续合入流程。实际结果和限制见 verification.md。

## 已修复发现

| 优先级 | 原问题 | 修正 |
|---|---|---|
| P1 | 覆盖查询未过滤分数来源、删除状态和租户，human/llm_judge 或其他 workspace 同名行可补确定性缺口 | 只认可当前 Run/workspace、未删除、有限且在有效范围内的 deterministic 分数；覆盖与质量统一使用该输入 |
| P1 | 空指标/空样本/全部豁免使 expected 为空并直接通过；畸形 UUID 可能导致 HTTP 500 | 显式 schema_version 和范围形状校验；零必需范围、未知格式、重复项、不合法豁免和快照不一致均报告失败 |
| P1 | engine 每次运行都覆盖 evaluation_scope，未完成 Run 重入可缩小原范围 | 原范围只写一次；新输入不一致时在答题前失败，保留原范围 |
| P1 | known-bad 只从覆盖检查排除，质量仍读取所有分数；越界样本能稀释低分，额外指标也可能错误阻断 | Agent 调用方按同一冻结非豁免范围筛选质量输入，沿用原阈值和公式；共享 Workflow 评分模块不变 |
| P1 | 仅验证候选覆盖，回归基线可缺范围/缺分，或缺少对应指标而被共享比较跳过 | 回归基线也要求完整范围证据和相同必需指标集，否则明确失败 |
| P1 | downgrade 直接删 scope 列，并被描述为无数据风险 | 有范围证据时拒绝删列；PG 实测证据和 revision 均保留；应用回退保留增量列 |
| P2 | 把所有 evaluator 都作为必需指标，使 LLM judge 失败新增为发布阻断，超出原定确定性切片 | 只冻结当前内置确定性实现的指标；judge 结果保留报告用途；自定义对象不能仅凭同名声明内置支持 |
| P2 | 读取 schema 顺带暴露 agent_snapshot 原始执行配置，与本次范围无关 | 读取 API 仅新增 evaluation_scope，保留原有 agent_snapshot 非公开边界 |

## 测试与工件调整

新增回归覆盖不可信/删除/跨租户分数、畸形和空范围、全部豁免、质量输入范围、基线、真实引擎异常/空输出、冻结冲突以及 LLM judge 不进入确定性门禁。原测试中的虚构 format_ok 替换为实际内置 is_json；测试替身继承 ExactMatchEvaluator，保持固定分数以继续隔离版本绑定断言。没有放宽既有质量、绑定或审计断言。

测试中对 JSON 列做变更时使用深拷贝/重新赋值并 flush，避免“修改嵌套字典但未实际落库”的伪回归。proposal/design/delta specs/tasks 已同步为实际契约。

本 change 证明当前内置确定性指标的版本绑定证据完整性，不代表外部 EvaluationBackend、自定义 evaluator、多次 Workflow 执行、完整 evaluator 版本体系或真实模型质量已认证。
