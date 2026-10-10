# Spec Delta

## MODIFIED Requirements

### Requirement: 平台中断尝试优先恢复原执行会话

平台 durable dispatch 对含受保护动作的中断尝试 SHALL 先验证原生 continuation、冻结图、身份与动作关联均已接通且状态可恢复；可恢复时 SHALL 沿用原 Run 与原 engine 会话继续原执行，已决动作回填真实结果、未决动作停止，MUST NOT 为恢复创建新模型 Run。续跑资格门 SHALL 包含冻结执行定义摘要校验：派发时记录的摘要（工具顺序/Schema、模型引用、persona/guardrail 引用）与续跑时重算的摘要不一致，或原 attempt 的引擎会话持久快照缺失/不可装载时，MUST NOT 续跑。仅普通聊天历史可加载 MUST NOT 作为原生可恢复证明；原生入口未实现或状态不可装载时 SHALL 保持 `reconciliation_required`。业务副作用 SHALL 以实际调用计数验证不重做；外部写已成功但回执丢失的续跑 SHALL 在下一动作前停止并保持待对账。

#### Scenario: 会话可恢复的中断尝试沿用原 Run
- **WHEN** 原生 continuation 已接通且原 attempt 的冻结状态、身份和动作关联均可验证
- **THEN** 沿用原 Run 与原 engine 会话原生续跑，台账仲裁使已决动作零重复调用

#### Scenario: 会话不可恢复的中断尝试保持保守待对账
- **WHEN** 中断尝试含受保护动作但无原生 continuation 或状态不可恢复
- **THEN** Task 保持 `reconciliation_required`，不以新 Run 盲目重放

#### Scenario: 历史存在但无原生续跑
- **WHEN** 中断尝试已有受保护动作且普通 SessionState 可加载
- **THEN** 不重新调用模型，Task 保持待对账

#### Scenario: 执行定义摘要漂移拒绝续跑
- **WHEN** 中断尝试的会话快照存在，但续跑时重算的执行定义摘要（工具顺序/Schema、模型引用、persona/guardrail 引用）与派发时记录的摘要不一致，或原摘要缺失
- **THEN** 不装载该会话、不调用模型，Task 保持 `reconciliation_required` 并记录漂移原因

#### Scenario: 外部写成功但回执丢失时续跑保守停止
- **WHEN** 可续跑的中断尝试在台账中存在已领取但结果未决的受保护动作（外部写已发生、回执未落）
- **THEN** 原生续跑装载原会话，但已决动作仅回填、未决动作不重做；执行在该动作边界停止，Task 进入 `reconciliation_required`，业务 API 调用计数不增加

#### Scenario: 续跑沿用原 Run 且身份在续跑前重验
- **WHEN** 满足全部续跑资格条件并原生续跑
- **THEN** 沿用原 Run 标识与原 engine 会话，不铸造新 attempt；Principal、部署与冻结版本的既 有准入重验先于续跑执行，重验失败时不进入续跑路径
