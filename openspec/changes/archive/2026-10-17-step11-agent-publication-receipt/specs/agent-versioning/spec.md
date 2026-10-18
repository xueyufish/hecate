# Spec Delta — agent-versioning

## MODIFIED Requirements

### Requirement: 发布与发布门禁挂钩

系统 SHALL 提供「发布」动作：将指定已提交版本设为该 Agent 的 `published_version` 指针（初始为空）。发布可配置评估门禁（复用 7.3a 机制）：未配置时发布直接生效；`warn` 模式 SHALL 计算并报告门禁结果但不阻断；`require` 模式在任何启用信号失败时 SHALL 拒绝发布。门禁 v1 默认关闭。门禁消费的候选评测 SHALL 限定为 Agent、版本、配置哈希和 workspace 均匹配的已完成运行（见 `evaluation-tasks` 的运行绑定要求）；不存在匹配版本的已完成运行时，`require` 模式 SHALL 明确拒绝发布（原因标明缺少匹配评测），不得回退到未绑定或旧版本的运行。基线比较 SHALL 使用当前 `published_version` 的匹配运行；不存在时基线信号按缺失处理，不得以其他版本运行冒充。漂移信号 SHALL 比较 Run 的数据集快照与当前内容；缺少必要快照时 SHALL 报告失败。候选运行 SHALL 进一步满足评测证据覆盖完整性：按其冻结范围，每个非豁免样本 × 每个必需确定性指标都存在本 workspace 未删除的有效确定性分数；存在证据缺失、或绑定运行缺少可核验的评测范围时，`require` 模式 SHALL 拒绝发布（覆盖与无法确认完整性分别标明原因），`warn` 模式 SHALL 报告但不阻断。空、畸形、未知格式或零必需样本/指标的范围 SHALL NOT 空转通过。质量判定 SHALL 使用冻结非豁免范围；启用回归时，基线 SHALL 具有完整范围证据及相同必需指标集，否则明确失败。证据缺失与质量失败 SHALL 分开报告。`force` 绕过 SHALL 在移动发布指针前成功记录审计（操作者、版本、门禁结果），且绕过的发布不得被记录或报告为"门禁通过"。历史运行 SHALL NOT 补造评测范围，必要时重新评测。

每次成功的发布 SHALL 额外落一条只追加的发布凭据记录（publication receipt），并在同一事务内先于移动 `published_version` 指针写入：记录 SHALL 保存 workspace、Agent 与 version、对齐该版本 content_hash 的 `agent_config_hash`、本次判定实际消费的 candidate 与 baseline Run（仅启用回归比较时记录 baseline） 的 id 及各自 `agent_config_hash`（无则为空）、本次门禁开始判定前冻结的 `gate_config` 快照、门禁报告快照、outcome、operator user_id 与时间；Run 身份 SHALL 取判定时实际选中者，SHALL NOT 在记录时重新查询 latest。outcome SHALL 为封闭四值之一：`passed`（require 全信号通过）、`warn_passed`（warn 模式放行）、`force_bypassed`（force 绕过，与既有 bypass 审计并存）、`gate_off`（门禁关闭、评测未参与判定）；`gate_off` 的记录与呈现 SHALL NOT 表述为"评测通过"。凭据记录写入失败时发布 SHALL NOT 生效（与指针移动保持原子）；require 拒绝路径 SHALL NOT 产生任何成功发布记录。凭据一经写入 SHALL NOT 被后续的任务修改、门禁配置修改或补跑评测改写；本能力引入前的历史发布 SHALL NOT 补造凭据。

#### Scenario: 发布移动指针

- **WHEN** Agent A 发布版本 v3
- **THEN** Agent A 的 published_version SHALL 变为 3，且不影响后续提交的新版本

#### Scenario: require 门禁阻断发布

- **WHEN** Agent A 配置 require 门禁且启用信号失败，尝试发布 v4
- **THEN** 发布 SHALL 被拒绝并返回门禁失败详情，published_version 保持不变

#### Scenario: 旧版本高分不放行新版本

- **WHEN** Agent A 仅存在绑定到 v1 的已完成评测且该评测全部通过，配置 require 门禁后尝试发布 v2
- **THEN** 发布 SHALL 被拒绝，原因标明 v2 缺少匹配的已完成评测；v1 的评测结果不参与 v2 的判定

#### Scenario: 匹配版本的评测参与判定

- **WHEN** Agent A 存在绑定到 v2 的已完成评测且启用信号失败，配置 require 门禁后尝试发布 v2
- **THEN** 门禁 SHALL 消费该 v2 运行并拒绝发布；同一评测结果在 `warn` 模式下 SHALL 报告但不阻断

#### Scenario: 无匹配运行时基线按缺失处理

- **WHEN** 发布 v3 时当前 `published_version` 为 v2 且不存在绑定到 v2 的运行
- **THEN** 基线信号 SHALL 以基线缺失处理并如实报告，不使用其他版本的运行充当基线

#### Scenario: 证据不完整时 require 拒绝

- **WHEN** 候选 v2 运行的冻结范围内存在缺证据的"样本 × 指标"组合（评测异常、生成失败或无分数行），配置 require 门禁后尝试发布 v2
- **THEN** 发布 SHALL 被拒绝，覆盖信号失败并定位缺失计数与样本；其余样本的满分不构成放行理由

#### Scenario: 历史无范围运行按无法确认处理

- **WHEN** 候选运行绑定于版本绑定能力引入、尚无冻结评测范围，配置 require 门禁后尝试发布
- **THEN** 发布 SHALL 被拒绝并以"无法确认完整性"为因；不按当前配置补造范围，重新评测可获得可核验范围

#### Scenario: 质量失败与证据缺失分别报告

- **WHEN** 候选运行范围内一个样本确定性指标低分（有效分但低于阈值），另一个样本缺分数行
- **THEN** 通过率信号报告质量失败，覆盖信号报告证据缺失，两者在门禁报告中分列，互不替代

#### Scenario: force 绕过留审计且不算通过

- **WHEN** require 门禁阻断后调用方以 `force` 发布 v2
- **THEN** 发布生效前 SHALL 成功记录审计（操作者、版本、被绕过的门禁结果），门禁报告中该发布 SHALL 标示为绕过而非通过

#### Scenario: 成功发布落凭据并锚定当时 Run

- **WHEN** require 门禁全信号通过后发布 v2，判定实际选中的 candidate Run 为 R5
- **THEN** 同一事务内先写入凭据记录（含 R5 的 id 与其 agent_config_hash、发布时 gate_config 与报告快照、outcome=passed、operator、时间）再移动指针；此后即使存在更新的已完成 Run，该记录的 Run 身份不变

#### Scenario: 凭据写入失败发布不生效

- **WHEN** 凭据记录写入因约束或存储失败
- **THEN** 发布整体失败，published_version 保持不变，不出现"指针已移动而无凭据"或"凭据存在而发布未生效"的中间态

#### Scenario: gate_off 不表述为评测通过

- **WHEN** 门禁关闭（未配置 evaluation_gate）时发布 v3
- **THEN** 凭据记录 outcome 为 gate_off，记录与查询呈现均为"评测未参与判定"，不出现"评测通过"语义

## ADDED Requirements

### Requirement: 发布历史只读查询

系统 SHALL 提供只读发布历史查询：按 Agent 返回其发布凭据记录，时间倒序、分页；查询及产生凭据的发布入口 SHALL 按 workspace 隔离，其他 workspace 的 Agent SHALL 表现为不存在。仅显式 system-scope 身份可跨 workspace；缺 workspace 的非系统身份 SHALL NOT 获得系统权限。返回字段 SHALL 限于凭据白名单（版本身份、Run 身份与配置哈希、gate_config 与报告快照、outcome、operator、时间），不含内部中间态。读取历史 SHALL NOT 重新计算门禁——返回的永远是发布时点的报告快照；本能力引入前的历史发布 SHALL NOT 补造凭据，查询结果为空属预期。回滚 SHALL NOT 产生发布记录（指针未移动）。

#### Scenario: 发布后修改不影响历史结论

- **WHEN** 发布 v2 成功后，Agent 的 evaluation_gate 被改为更严格配置、评测任务被修改且补跑了一个新的高分 v2 Run
- **THEN** 历史查询中该发布记录仍为当时的 outcome、candidate_run_id 与报告快照，新 Run 与新配置不出现在该记录中

#### Scenario: 分页与排序

- **WHEN** Agent 有 12 条发布记录，请求第 1 页（每页 10 条）
- **THEN** 通过 limit=10、offset=0 返回最近 10 条（时间倒序）与 total；offset=10 返回其余 2 条

#### Scenario: workspace 隔离

- **WHEN** workspace W2 的调用方查询 workspace W1 的 Agent 的发布历史
- **THEN** 响应为不存在（404），不泄露该 Agent 是否有发布记录

#### Scenario: 读取不重算门禁

- **WHEN** 查询发布历史时，该 Agent 当前的评测与门禁状态已与发布时不同
- **THEN** 返回内容为凭据中的快照，查询过程不触发任何门禁信号计算

#### Scenario: 回滚不产生发布记录

- **WHEN** 调用 rollback 创建新版本（指针未移动）
- **THEN** 发布历史不新增记录；仅当该新版本随后被显式发布时才产生新凭据

#### Scenario: 历史发布不补造凭据

- **WHEN** 查询一个在本能力引入前完成过发布的 Agent 的历史
- **THEN** 该早期发布不出现凭据记录，也不以当前状态补造


#### Scenario: 有凭据时拒绝有损降级

- **WHEN** agent_publications 已存在任意发布凭据（包括软删除行），尝试降级删除该表
- **THEN** 迁移 SHALL 拒绝并保留凭据、发布指针与迁移版本；仅空表允许降级和重新升级

#### Scenario: 缺少 workspace 的非系统身份

- **WHEN** 缺少 workspace 的 JWT、SSO、LDAP 或 workspace API key 请求发布或读取发布历史
- **THEN** 请求 SHALL 返回 404，不产生凭据或 bypass 审计；仅显式 system API key 可以按系统范围访问

#### Scenario: 冻结实际判定配置

- **WHEN** 门禁开始计算后配置发生修改或出现更新的候选 Run
- **THEN** 凭据 SHALL 保留判定开始时的配置与实际消费的 Run，不采用事后 latest；未启用回归比较时 baseline 字段为空
