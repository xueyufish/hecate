# feature-inventory-governance Delta

## ADDED Requirements

### Requirement: Schema v2 governance fields

功能清单 MUST 升级到 schema 2:每条目在既有字段之上新增治理字段 `responsibility`(枚举:`platform-guarantee` / `shared-contract` / `provider-guarantee` / `optional-ecosystem`)、`implementation_mode`(枚举:`builtin` / `in-process-plugin` / `out-of-process` / `external-service` / `asset`)、`provider_or_adapter`、`enforcement_point`、`state_owner`、`milestone`、`superseded_by`。校验器 MUST 拒绝不在枚举内的取值;不适用字段 MUST 允许显式标注不适用值而非留空伪造待填;`maturity` MUST 独立表示验证程度,MUST NOT 由 `status: delivered` 自动推导 `production`;延期与退役状态归 `status`,MUST NOT 混入 `implementation_mode`。历史条目未填治理字段时 MUST 作为显式欠账报告,校验器 MUST NOT 自动填充或批量推导。

#### Scenario: Invalid enum value is rejected

- **WHEN** 清单条目的 `responsibility` 或 `implementation_mode` 取了枚举外的值
- **THEN** 校验以非零退出码失败并指出条目 ID、字段与非法取值

#### Scenario: Explicit not-applicable is accepted

- **WHEN** 某条目的 `enforcement_point` 显式标注不适用值(如 `n/a`)
- **THEN** 校验通过且该字段不被记为缺失欠账

#### Scenario: Historical entries report debt without failing non-strict mode

- **WHEN** 对含未填治理字段的历史条目运行非严格校验
- **THEN** 逐条目报告治理字段欠账,退出码为 0;严格模式下欠账计为失败

#### Scenario: Delivered status does not imply production maturity

- **WHEN** 某条目 `status: delivered` 而 `maturity` 未声明
- **THEN** 校验器不自动赋予 `production`,该条目按 maturity 缺失规则处理

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
