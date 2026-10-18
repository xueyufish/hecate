# Spec Delta — evaluation-tasks

## ADDED Requirements

### Requirement: 绑定运行冻结评测范围

绑定 AgentVersion 的评测运行 SHALL 在实际样本和豁免确定后、答题前把版本化 evaluation_scope 保存到 Run：schema_version、实际 item_ids、当前受支持内置确定性 required_metrics、范围内 exempted_item_ids 和 dataset_snapshot_hash。tags 和快照/live known-bad 来源 SHALL 按既有语义结清。后续任务、数据集、草稿变更 SHALL NOT 改写范围；重入时范围冲突 SHALL 失败并保留原证据。未绑定运行保持既有行为，历史运行 SHALL NOT 补造范围。

#### Scenario: 范围在生成答案前冻结

- **WHEN** 实际选中十项，其中一项在快照中 known-bad，运行同时配置 exact_match 和 LLM judge
- **THEN** 答题前保存十个 item_ids、一个豁免 id 和 exact_match 必需指标，LLM judge 不进入必需指标

#### Scenario: tags 与快照豁免决定范围

- **WHEN** live 路径按 tags 只选中部分项，快照 known-bad 与活行不一致
- **THEN** 范围只包含实际选中项，豁免依据快照且限制为选中项的子集

#### Scenario: 配置变化不覆盖已保存范围

- **WHEN** 相同 Run 重入但 tags、样本或必需指标改变
- **THEN** 执行在生成答案前失败，原 evaluation_scope 保持不变

#### Scenario: 未绑定与历史保持原状

- **WHEN** live 评测未声明版本，或读取已有无范围运行
- **THEN** live 行为不变，历史不回填为完整证据

### Requirement: 覆盖完整性按冻结范围逐项核验

每个非豁免样本 × 必需确定性指标 SHALL 具备本 Run、本 workspace、未删除、source=deterministic 且数值有效的分数。异常、空输出、缺行、负分、无效值或其他来源 SHALL NOT 填补确定性缺口。覆盖按 pair 去重，重复分数 SHALL NOT 填补另一指标。缺证据与有效低分 SHALL 分别报告。缺失、畸形、未知版本范围、零必需样本/指标或全部豁免 SHALL NOT 空转通过。质量分数 SHALL 仅取冻结非豁免范围；共享 Workflow 评分行为不变。

#### Scenario: 完整分数通过覆盖检查

- **WHEN** 所有必需 pair 均有有效确定性分数
- **THEN** 覆盖通过，再按质量阈值判断，有效低分不会被当成缺证据

#### Scenario: 人工或 judge 分数不能填缺口

- **WHEN** exact_match 的确定性结果缺失，但同名 human 或 llm_judge 分数存在
- **THEN** 该 pair 仍缺失，其他高分和重复分数也不能补齐

#### Scenario: evaluator 失败或空输出不放行

- **WHEN** 十项中一项 evaluator 抛错或返回空输出，其他九项全过
- **THEN** 保留逐项失败隔离，但覆盖缺一项，整体门禁不得以剩余九项高分通过

#### Scenario: 范围不可核验则报告失败

- **WHEN** 范围缺字段、格式无效、版本未知、零必需指标或全部样本豁免
- **THEN** 覆盖报告明确失败原因，不返回成功或因解析失败产生 HTTP 500

#### Scenario: 豁免和越界分数不改变质量

- **WHEN** 豁免样本有低分，或分数行包含范围之外的样本/指标
- **THEN** 这些行不参与本次候选的质量通过率及回归平均分

#### Scenario: 缺失计数完整且定位有界

- **WHEN** 超过二十个 pair 缺失
- **THEN** 报告保留真实 missing_count，并仅展示前二十个定位
