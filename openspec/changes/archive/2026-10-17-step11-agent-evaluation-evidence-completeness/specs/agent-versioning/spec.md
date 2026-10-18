# Spec Delta — agent-versioning

## MODIFIED Requirements

### Requirement: 发布与发布门禁挂钩

系统 SHALL 提供「发布」动作：将指定已提交版本设为该 Agent 的 `published_version` 指针（初始为空）。发布可配置评估门禁（复用 7.3a 机制）：未配置时发布直接生效；`warn` 模式 SHALL 计算并报告门禁结果但不阻断；`require` 模式在任何启用信号失败时 SHALL 拒绝发布。门禁 v1 默认关闭。门禁消费的候选评测 SHALL 限定为 Agent、版本、配置哈希和 workspace 均匹配的已完成运行（见 `evaluation-tasks` 的运行绑定要求）；不存在匹配版本的已完成运行时，`require` 模式 SHALL 明确拒绝发布（原因标明缺少匹配评测），不得回退到未绑定或旧版本的运行。基线比较 SHALL 使用当前 `published_version` 的匹配运行；不存在时基线信号按缺失处理，不得以其他版本运行冒充。漂移信号 SHALL 比较 Run 的数据集快照与当前内容；缺少必要快照时 SHALL 报告失败。候选运行 SHALL 进一步满足评测证据覆盖完整性：按其冻结范围，每个非豁免样本 × 每个必需确定性指标都存在本 workspace 未删除的有效确定性分数；存在证据缺失、或绑定运行缺少可核验的评测范围时，`require` 模式 SHALL 拒绝发布（覆盖与无法确认完整性分别标明原因），`warn` 模式 SHALL 报告但不阻断。空、畸形、未知格式或零必需样本/指标的范围 SHALL NOT 空转通过。质量判定 SHALL 使用冻结非豁免范围；启用回归时，基线 SHALL 具有完整范围证据及相同必需指标集，否则明确失败。证据缺失与质量失败 SHALL 分开报告。`force` 绕过 SHALL 在移动发布指针前成功记录审计（操作者、版本、门禁结果），且绕过的发布不得被记录或报告为"门禁通过"。历史运行 SHALL NOT 补造评测范围，必要时重新评测。

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
