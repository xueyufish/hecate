## ADDED Requirements

### Requirement: 等待唤醒保持前序动作与可信宿主状态

宿主 SHALL 在新等待唤醒 attempt 中保留前序工具的逻辑动作身份并回填真实结果；MUST NOT 因新 Run 或 checkpoint 清理窗口重新执行前序已完成写操作。宿主内部 grant 和动作来源 SHALL 仅由可信持久状态生成。并发唤醒 SHALL 通过 revision CAS 只应用一个；命令重放 SHALL 比对 Run、issuer、token、载荷和期限。

#### Scenario: 写入后等待再唤醒
- **WHEN** 前序业务写入已完成，后续工具等待审批或输入并被唤醒
- **THEN** 前序写入调用次数保持一次，后续工具按合法唤醒执行

#### Scenario: 伪造内部 grant
- **WHEN** 提交请求包含宿主内部 grant 或动作来源字段
- **THEN** 拒绝提交且无工具派发

#### Scenario: 并发唤醒与异体重放
- **WHEN** 两个命令使用相同 token 并发唤醒，或已应用 command ID 被不同请求重用
- **THEN** 至多一个命令应用，异体重放冲突，原等待和回执不被覆盖

### Requirement: 未知动作停止后续工具且受管补传按来源隔离

动作结果未知、台账结果写失败或授权拒绝时，宿主 SHALL 停止本次执行的后续工具；未知动作 SHALL 保留待对账。受管通道 SHALL 仅上传受管任务的事件，独立任务或单个失败流 MUST NOT 阻塞其他受管 Run 的历史补传。

#### Scenario: 前序写入结果未知
- **WHEN** 业务写入已发生但回执失败或返回未知
- **THEN** 后续业务工具零调用，任务待对账，恢复不重复未知写

#### Scenario: 混合来源补传
- **WHEN** 宿主同时保存独立和受管任务，且受管任务存在历次 Run
- **THEN** 仅上传受管各 Run 的事件，一个流失败不影响其他流

### Requirement: 恢复绑定接收时执行定义和可信调用主体

Runner SHALL 从可信装配写入执行定义摘要，包含 manifest、工具顺序/Schema、模型引用和业务 API 绑定。恢复 SHALL 验证摘要一致；摘要缺失或漂移 SHALL 将可调度任务转待对账，等待任务 SHALL 拒绝唤醒且保留 token。终态仍允许 owner 查询。所有任务列表、动作及事件 SHALL 只对 owner 与当前可信数据域匹配的身份可见。

#### Scenario: 升级改变旧执行定义
- **WHEN** 非终态旧任务缺少原定义摘要或当前配置与摘要不符
- **THEN** 不派发新工具，不凭当前配置推定旧执行可恢复

#### Scenario: 跨身份读取持久任务
- **WHEN** 另一个有效身份读取其他 owner 的 Task/Action/事件
- **THEN** 列表不包含该任务，具名访问被拒绝

### Requirement: 生产受管 Lease 绑定完整宿主身份

受管 CLI SHALL 在受保护动作派发时同时校验 lease 的 issuer、host subject、workspace tenant、部署 audience、scope、期限及 nonce；即使签名有效，身份不匹配 MUST 拒绝且不调用业务写 API。

#### Scenario: 同签名材料的其他主体或租户
- **WHEN** 当前 Lease 签名有效但 issuer、subject 或 tenant 与已接入宿主不符
- **THEN** 受保护业务 API 调用次数为零且执行失败
