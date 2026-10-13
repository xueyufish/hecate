## ADDED Requirements

### Requirement: 最终输出决定入口终态

入口 SHALL 在最终输出校验及有限修复成功后才发布成功投影与摘要；校验终止 SHALL 携带停止类别及真实关联。流式完成 SHALL 保存最终内容的有界摘要。流式与非流式停止及结构化输出 SHALL 保留相同的关联可见性，包括缺关联的显式标记。

#### Scenario: 最终校验拒绝
- **WHEN** 引擎已生成内容，但声明的输出 schema 经允许的修复后仍不满足
- **THEN** 客户端收到 invalid_output 与真实关联，Run 投影为 failed，不能保留 succeeded

#### Scenario: 流式成功摘要
- **WHEN** 已登记 Run 的流式执行正常耗尽
- **THEN** Run 投影为 succeeded 并包含最终内容摘要

#### Scenario: 非流式停止转换
- **WHEN** 已登记 Run 的执行以预算、取消、迭代上限或其他停止类别返回非流式响应
- **THEN** 终止载荷包含相同 Task/Run 引用，异常转换不丢失引用

### Requirement: 关联缺口与内部事件隔离

缺少 Run 的业务入口 SHALL 明确标记关联缺失并保留已实际登记的 Task 引用；内部 engine 事件集合 MUST NOT 进入返回结果，即使登记失败或仅登记 Task。平台信封的持久化 SHALL 由共享入口执行边界承担，不依赖特定协议消费者再次写入。

#### Scenario: 缺关联的同步输出
- **WHEN** 同步执行没有完整 Task/Run 关联
- **THEN** 结果保持业务响应，内部 engine 事件集合被剥离，关联缺口明确

#### Scenario: 非 HTTP 流式消费
- **WHEN** 任意共享入口消费者排空已登记 Run 的流式事件
- **THEN** 新读取上下文能按 Run 读取持久信封，HTTP 渲染不重复追加
