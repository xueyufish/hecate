# feature-inventory-governance Specification

## Purpose

定义结构化功能清单(`docs/features/feature-inventory.yaml` 与 `docs/features/feature-catalog.md`)的提取与校验契约:字段所有权划分、extract 的按 ID 非破坏合并语义、两份文件间交付状态矛盾的显式失败规则,以及生成幂等性要求,使清单工具可以安全地反复运行而不丢失手工维护的治理数据。

## Requirements

### Requirement: Field ownership between catalog and inventory

功能清单工具 MUST 实施并成文以下字段所有权:`docs/features/feature-catalog.md` 拥有索引镜像字段(`title`、`phase`、`category`),extract 运行时按 catalog 刷新既有条目的这些字段并报告变更;`docs/features/feature-inventory.yaml` 拥有机器可读状态与治理数据(`status`、`maturity`、`dependencies`、`evidence`、`acceptance`)及条目上的任何其他键。extract MUST NOT 改写既有条目的 YAML 拥有字段;新导入条目按 seed 规则初始化(`status` 取自 catalog 的 ✅ 标记,治理字段置空)。生成的 YAML MUST 携带说明字段所有权的头注释。

#### Scenario: Extract preserves hand-maintained fields

- **WHEN** 对包含已填写 `maturity`/`dependencies`/`evidence`/`acceptance` 及扩展键的清单运行 extract
- **THEN** 这些字段的值逐条目逐字段保持不变,仅索引镜像字段按 catalog 刷新且变更被报告

#### Scenario: First import seeds governance fields empty

- **WHEN** catalog 中出现清单里不存在的新 ID
- **THEN** 新条目被追加,`status` 按 ✅ 标记初始化,治理字段为空值,既有条目不受影响

### Requirement: Extract merge is explicit and non-destructive

`extract` MUST 按 ID 合并而非全量重建:catalog 解析失败或 ID 重复时 MUST 失败且不写任何文件;catalog 中已移除而清单仍存在的 ID MUST 被保留并显式报告(不静默删除,由 check 的 ID 漂移检测持续暴露);对同一输入连续运行两次 MUST 产生字节一致的 YAML。当 catalog 的 ✅ 标记与清单 `status` 矛盾(catalog 标 ✅ 而 YAML 非 `delivered`,或 catalog 无 ✅ 而 YAML 为 `delivered`)时,extract MUST 以非零退出码失败、逐 ID 报告两个取值且不写任何文件;catalog 无 ✅ 且 YAML 为 `delivered` 之外的合法精化(如 `research-candidate`)MUST NOT 被视为矛盾。

#### Scenario: Status contradiction fails extract without writing

- **WHEN** catalog 某行标 ✅ 而清单同 ID `status` 非 `delivered`(或反向:catalog 无 ✅ 而 YAML 为 `delivered`)
- **THEN** extract 以非零退出码失败,报告每个矛盾 ID 的 catalog 与 YAML 取值,清单文件内容不变

#### Scenario: Legitimate refinement is not a contradiction

- **WHEN** catalog 行无 ✅ 而清单同 ID `status` 为 `research-candidate`
- **THEN** extract 正常完成,该条目的 YAML 拥有字段保持不变

#### Scenario: Catalog removal is reported, not deleted

- **WHEN** 清单中某 ID 已不在 catalog 表中出现
- **THEN** extract 保留该条目并显式报告,不将其从 YAML 删除

#### Scenario: Repeated extract is idempotent

- **WHEN** 对同一 catalog 与清单状态连续运行两次 extract
- **THEN** 第二次运行后 YAML 内容与第一次完全一致(字节相等)

### Requirement: Check detects delivery-status contradictions

`check` MUST 在既有校验(重复 ID、依赖存在性与环、production 缺证据、ID 集合漂移、缺失证据/验收告警)之外,增加与 extract 相同的交付状态矛盾检测:catalog 的 ✅ 标记与清单 `status` 在上述两个矛盾方向不一致时 MUST 报 ERROR;合法精化(如 `research-candidate`)MUST NOT 报错。check 的非严格模式对缺失证据/验收的 WARNING 行为 MUST 保持不变。

#### Scenario: Check reports contradiction as error

- **WHEN** 对包含交付状态矛盾的 catalog/清单运行 check
- **THEN** check 以非零退出码失败并逐 ID 报告矛盾,其余既有校验行为不变

#### Scenario: Existing clean state passes unchanged

- **WHEN** 对当前无矛盾的仓库状态运行 check(含 CI 的非严格模式)
- **THEN** 结果与引入矛盾检测前一致(相同的 errors 与 warnings 计数)
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

### Requirement: Plan dispositions and capability registrations are explicit

§六映射 MUST 在清单和受管文档保留 Feature ID、交付历史，并登记处置类别、当前实现/提供方、目标边界、状态 owner、强制执行点及里程碑。已有能力的独立部署形态 MUST NOT 由未来目标推断；未验证使用量、成熟度与支持窗口 MUST 显式标注。每个能力契约登记 MUST 包括契约 owner、公开契约、发布单元、状态 owner、版本与支持窗口。十类已交付候选 MUST 有代码/数据 owner、安装与挂载证据、使用量验证状态、迁移兼容与回退窗口的可追溯记录。

#### Scenario: Planned backend does not overwrite delivered implementation

- **WHEN** 将已交付的内置能力规划为可替换外部后端
- **THEN** 交付状态与当前实现记录保留，目标形态和未支持能力另行成文

#### Scenario: Missing disposition or contract owner is detected

- **WHEN** 一个声明映射的条目缺处置数据，或契约登记缺 owner
- **THEN** 校验失败并指出对应条目或能力
