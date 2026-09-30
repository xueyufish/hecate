# feature-inventory-governance Delta

## MODIFIED Requirements

### Requirement: Strict admission for new, changed, and production entries

check MUST 提供"增量严格"规则:新导入条目与发生变更的条目 MUST 具备完整治理字段与验收字段(`evidence`、`acceptance`)方可通过严格校验;声明 `maturity: production` 的条目 MUST 具备可核验证据引用,否则 MUST 失败。该规则 MUST NOT 要求一次补齐全部历史条目:历史未补条目保持显式欠账(WARNING 或严格模式失败,由 CI 选择的模式决定),MUST NOT 以一次全量标记 production 消除告警。

#### Scenario: New entry without acceptance fails strict check

- **WHEN** 严格模式下新导入条目缺少 `acceptance`
- **THEN** check 失败并指出该 ID 缺失的字段

#### Scenario: Production claim requires evidence

- **WHEN** 某条目声明 `maturity: production` 而无证据引用
- **THEN** check 以错误失败,无论是否严格模式

#### Scenario: Historical debt stays visible

- **WHEN** CI 以非严格模式运行且历史条目缺证据
- **THEN** 欠账以 WARNING 持续报告,构建不失败,告警计数不得被批量清零掩盖

增量校验 MUST 接受可信 Git 基线引用并比较每个条目的语义内容；新增/变更项缺完整治理字段或 evidence/acceptance 时 MUST 失败，未变历史欠账 MUST 继续报告但不阻断增量校验。基线不可读取或内容无效 MUST 失败。基线中已有 ID MUST 保留，退役通过显式状态和处置记录表达；同时从 catalog 与 inventory 删除既有 ID MUST 失败。无基线的 --strict MUST 保留全量校验。CI MUST 为 PR、push 与 merge_group 提供相应基线。空白字符串、空集合 MUST 记为缺失；production 的 pending/n/a MUST NOT 视为证据。

#### Scenario: Changed entry fails while unchanged debt remains visible
- **WHEN** 使用基线校验仅修改一个缺验收字段的条目
- **THEN** 校验以该 ID 与缺字段失败，未变历史条目的欠账只报告

#### Scenario: Invalid baseline fails closed
- **WHEN** 指定的 Git 基线或基线清单不可读取
- **THEN** 校验失败而不退回非严格模式

#### Scenario: Complete changed entry passes with historical debt
- **WHEN** 修改项治理与验收字段完整，其他历史项未变但仍缺字段
- **THEN** 增量校验通过且历史欠账持续可见

### Requirement: Research and Labs entries carry lifecycle governance

`status` 为研究/实验类(如 `research-candidate`)的条目 MUST 记录生命周期治理字段:验证负责人(`research_owner`)、验证假设(`hypothesis`)、投入边界(`investment_boundary`)、复核触发条件(`review_trigger`)与退出决定(`exit_decision`);字段允许显式标注待定,但 MUST NOT 全部缺省。晋级 MUST 以可复现结果与契约/安全验收为前置,缺证据时 MUST 保持实验状态或归档,MUST NOT 仅因文档编辑进入 delivered。

#### Scenario: Research candidate without governance fields fails strict check

- **WHEN** 严格模式下研究类条目缺少全部生命周期治理字段
- **THEN** check 失败并指出该条目

#### Scenario: Pending fields are explicit

- **WHEN** 研究类条目将 `exit_decision` 显式标为待定
- **THEN** 校验通过,且该值不被记为缺失欠账

#### Scenario: Promotion requires evidence

- **WHEN** 研究类条目拟改为 delivered 而无可复现结果与验收引用
- **THEN** 校验失败,状态修改被阻止

研究条目生命周期字段 MUST 逐项检查缺失；pending 是显式待定但空白不算填写。基线状态为研究/实验类或当前保留研究生命周期字段的条目晋级 delivered 时 MUST 要求 evidence 与 acceptance，MUST NOT 用 catalog 同时添加 ✅ 替代结果与验收引用。该晋级门禁 MUST 在非严格模式同样生效。

#### Scenario: Matching catalog mark cannot bypass promotion evidence
- **WHEN** 研究条目改为 delivered 且 catalog 同步添加 ✅，但缺结果或验收引用
- **THEN** 校验失败并指出晋级证据缺失

#### Scenario: Partial lifecycle debt is reported
- **WHEN** 研究条目仅有一个生命周期字段，其他字段缺失
- **THEN** 逐字段报告欠账，严格范围内失败

### Requirement: Managed generation with per-field drift detection

catalog 与 roadmap 中的受管区域 MUST 以显式区域标记界定,由清单工具从 YAML 确定性生成;生成 MUST 幂等(同输入字节一致)且 MUST NOT 触碰区域外的手写正文。迁移期与迁移后,校验器 MUST 逐字段比较受管区域的当前内容与再生成结果:差异 MUST 报告到条目 ID 与字段级,迁移完成后任何漂移 MUST 使 check 失败。受管区域内的手工编辑 MUST 被漂移检测暴露,不允许静默覆盖或静默接受。

#### Scenario: Regeneration is idempotent

- **WHEN** 对同一 YAML 与文档状态连续运行两次受管生成
- **THEN** 受管区域内容字节一致,区域外正文不变

#### Scenario: Hand edit inside governed region is detected

- **WHEN** 有人绕过生成流程直接修改受管区域内的条目内容
- **THEN** check 报告该条目该字段的漂移并失败,指出应以清单为唯一事实源修正

#### Scenario: Drift report is field-level

- **WHEN** 受管区域内容与 YAML 生成结果不一致
- **THEN** 报告精确到条目 ID 与字段名(如 `2.10b: milestone drifted`),不要求全文 diff 阅读

漂移 MUST 报告所有变化的 ID 与字段，行增删及正文变化须显式列出。正式文档的必需受管区域 MUST 存在且不可重复，并保持已完成迁移的 strict=true；删除区域或降为报告模式 MUST 失败。受管注册数据和输出表头/分隔行/行宽 MUST 一致，生成 MUST 在结构错误时拒绝写入。

#### Scenario: Multiple field changes are all reported
- **WHEN** 同一受管区域的多个 ID 或多个字段被篡改
- **THEN** check 报告全部条目和字段并失败

#### Scenario: Missing managed region fails
- **WHEN** 正式文档删除一个必需受管区域或关闭其严格属性
- **THEN** check 与 sync 失败，不把该文档当成没有受管内容

#### Scenario: Misaligned registry row fails before writing
- **WHEN** 注册数据的某行列数与表头不一致
- **THEN** 生成拒绝写入并指出表和行

## ADDED Requirements

### Requirement: Plan dispositions and capability registrations are explicit

§六映射 MUST 在清单和受管文档保留 Feature ID、交付历史，并登记处置类别、当前实现/提供方、目标边界、状态 owner、强制执行点及里程碑。已有能力的独立部署形态 MUST NOT 由未来目标推断；未验证使用量、成熟度与支持窗口 MUST 显式标注。每个能力契约登记 MUST 包括契约 owner、公开契约、发布单元、状态 owner、版本与支持窗口。十类已交付候选 MUST 有代码/数据 owner、安装与挂载证据、使用量验证状态、迁移兼容与回退窗口的可追溯记录。

#### Scenario: Planned backend does not overwrite delivered implementation
- **WHEN** 将已交付的内置能力规划为可替换外部后端
- **THEN** 交付状态与当前实现记录保留，目标形态和未支持能力另行成文

#### Scenario: Missing disposition or contract owner is detected
- **WHEN** 一个声明映射的条目缺处置数据，或契约登记缺 owner
- **THEN** 校验失败并指出对应条目或能力
