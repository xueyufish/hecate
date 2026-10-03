## ADDED Requirements

### Requirement: 宿主执行与证据可观测且受身份隔离

宿主 MUST 在接受运行前持久保存准入记录；运行、事件、产物与取消 MUST 限定为原 principal 且当前数据域覆盖原运行数据域。参数校验 MUST 先于业务分发；endpoint 模式 MUST 实际调用配置模型，失败不得静默回退 Stub。取消 MUST 记录请求，并在工具调用边界生效；不能承诺撤回已发生调用。

#### Scenario: 他人或匿名查询 Run

- **WHEN** 匿名或其他 principal 查询已有 Run 或其产物
- **THEN** 拒绝，不返回原运行数据

#### Scenario: 证据不可写

- **WHEN** 准入证据写入失败
- **THEN** 不接受 Run，不调用模型或工具
