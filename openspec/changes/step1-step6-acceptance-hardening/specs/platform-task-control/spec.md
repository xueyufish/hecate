## ADDED Requirements

### Requirement: 命令重放绑定授权资源及原请求

平台 SHALL 先核实 workspace 中的 task，再返回任何既有命令回执。相同 command ID SHALL 绑定 task、issuer、kind、payload、schema、token、期限与 expected revision；任何差异 MUST 拒绝，未应用命令重试 SHALL 使用原 issued_at 和 Run 绑定。

#### Scenario: 跨 workspace 或异体重放
- **WHEN** 调用方重用其他资源的 command ID 或改变原命令参数
- **THEN** 访问失败或命令冲突，不暴露其他资源回执且无执行效果

### Requirement: 回调可信子事实不可由载荷替换

平台 SHALL 校验等待契约中的完整 child 引用，包括 kind、issuer 和 ID。回调提供的附加数据 MUST NOT 覆盖平台核实的 child ID 或 state。相同 command ID 重放 SHALL 校验完整原请求，不改变原回执。

#### Scenario: 引用或结果伪造
- **WHEN** 回调使用同 ID 的其他签发域引用或覆盖 child_outcome 的可信字段
- **THEN** 引用不匹配被拒绝；附加数据不能改变可信 child ID/state

### Requirement: 派发使用仍有效的冻结身份与部署版本

平台 SHALL 在运行前重验 Principal 有效性、workspace 和 admitted Run 的部署/版本快照。实际执行 SHALL 使用该版本的工具、模型、persona 和资源引用；失效或版本漂移 MUST 在模型/工具派发前进入待对账。

#### Scenario: 提交后身份撤销或配置漂移
- **WHEN** 排队期间 Principal 被撤销或冻结部署/版本改变
- **THEN** 不调用 Runtime，Task 进入待对账并记录原因

## MODIFIED Requirements

### Requirement: 平台中断尝试优先恢复原执行会话

平台 durable dispatch 对含受保护动作的中断尝试 SHALL 先验证原生 continuation、冻结图、身份与动作关联均已接通且状态可恢复；可恢复时 SHALL 沿用原 Run 与原 engine 会话继续原执行，已决动作回填真实结果、未决动作停止，MUST NOT 为恢复创建新模型 Run。仅普通聊天历史可加载 MUST NOT 作为原生可恢复证明；原生入口未实现或状态不可装载时 SHALL 保持 `reconciliation_required`。业务副作用 SHALL 以实际调用计数验证不重做。

#### Scenario: 会话可恢复的中断尝试沿用原 Run
- **WHEN** 原生 continuation 已接通且原 attempt 的冻结状态、身份和动作关联均可验证
- **THEN** 沿用原 Run 与原 engine 会话原生续跑，台账仲裁使已决动作零重复调用

#### Scenario: 会话不可恢复的中断尝试保持保守待对账
- **WHEN** 中断尝试含受保护动作但无原生 continuation 或状态不可恢复
- **THEN** Task 保持 `reconciliation_required`，不以新 Run 盲目重放

#### Scenario: 历史存在但无原生续跑
- **WHEN** 中断尝试已有受保护动作且普通 SessionState 可加载
- **THEN** 不重新调用模型，Task 保持待对账
