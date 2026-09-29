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
