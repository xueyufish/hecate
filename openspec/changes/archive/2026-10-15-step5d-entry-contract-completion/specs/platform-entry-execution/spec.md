# Spec Delta

## ADDED Requirements

### Requirement: 失败执行形成完整终态信封

同步与流式执行失败时,入口执行服务 SHALL 将已排空可得的引擎事件映射为平台信封,并追加一条终态信封到该 Run 的事件存储;终态信封 MUST 携带 terminal state、停止类别(cancelled、iteration_limit、budget_exhausted 等)与不泄漏内部异常细节的安全错误摘要。终态信封与失败前的流事件 SHALL 可在同一 Run 游标分页中读取;失败执行 MUST NOT 产生 succeeded 终态或成功摘要。

#### Scenario: 同步执行异常仍可读取事件与终态

- **WHEN** 经实际入口的同步执行在引擎阶段抛出异常,请求以失败响应结束
- **THEN** 该 Run 的事件页包含异常前已排空引擎事件映射的信封和一条终态信封;终态信封的 state 为 failed 且停止类别正确,错误摘要不含堆栈或内部字段

#### Scenario: 流式中途异常写入终态信封

- **WHEN** 流式执行在若干 chunk 已下发后异常中断
- **THEN** 已下发事件的信封保持可读,事件存储追加失败终态信封,Run 投影进入 failed/cancelled 且与信封一致

#### Scenario: 停止类别区分取消与失败

- **WHEN** 执行分别以用户取消、迭代上限和预算耗尽停止
- **THEN** 终态信封与 Run 投影分别携带 cancelled、iteration_limit、budget_exhausted 类别,不统一折叠为 execution_failed

#### Scenario: 任务取消和生成器关闭仍完成收尾

- **WHEN** 流式执行收到 asyncio 任务取消或消费方关闭生成器
- **THEN** 入口保存一条 cancelled 终态和一致投影，不因取消异常不属于普通 Exception 而丢失收尾；此确定性行为不代替 G3 的真实 TCP 断连认证

#### Scenario: 公开 Run 查询读取同一持久事件流

- **WHEN** 客户端经 workspace 授权查询入口 Run 的事件分页或订阅
- **THEN** 返回同一入口存储的流事件和终态信封，游标不漏项，已消费终态后订阅结束；公开响应隐藏 backend detail 和被拒绝的原始产物候选，跨 workspace 请求不得读取这些事实

### Requirement: 登记与失败事实独立于调用方事务

Task/Run 登记、终态投影与事件写入的事务所有权 SHALL 显式区分:平台登记与终态投影 MUST NOT 依赖调用方请求事务的提交,调用方外层事务回滚后,已发生的执行登记、失败终态投影与事件信封 SHALL 仍可按 workspace 查询。持久性修复 MUST NOT 提交调用方的业务事务,也不得借登记写入把调用方未完成的业务数据一并持久化。

#### Scenario: 外层回滚后登记与失败事实仍可查询

- **WHEN** 入口执行(含引擎异常)完成后,调用方因后续处理失败触发外层事务回滚
- **THEN** Task 登记、Run 记录、失败终态投影与事件信封在新数据库会话中仍可查询;同时调用方事务中未提交的业务写入未落库

#### Scenario: 登记失败不拖垮执行也不伪报关联

- **WHEN** 登记存储不可用或登记被注册表拒绝
- **THEN** 执行按既有 fail-open 语义继续或失败,结果携带显式 missing 标记与原因,不报告未记录的关联

### Requirement: 声明产物引用进入公开契约

执行结果声明的 output artifact 引用 SHALL 映射进公开契约:经 workspace 归属校验后记录到 Run 投影与终态信封,并可经 Run 查询路径读取。归属不明、跨 workspace 或与声明不符的产物引用 MUST 显式拒绝并留失败或告警证据,不得静默丢弃或降级为普通文本摘要;产物引用的读取 MUST 沿用 workspace 作用域的访问校验。

#### Scenario: 合法产物引用可经公开路径读取

- **WHEN** 执行成功且结果声明了同 workspace 的产物引用
- **THEN** Run 投影与终态信封携带该产物引用,按 Run 查询可读到与声明一致的引用

#### Scenario: 跨 workspace 产物引用被拒绝

- **WHEN** 结果声明的产物引用不属于本次执行的 workspace
- **THEN** 该引用被拒绝且执行侧留有可查证据,公开契约不出现越权产物引用

#### Scenario: 相同 issuer 不代替归属验证

- **WHEN** 声明与本次 Task/Run 有相同 issuer 的未知、已删除或其他 workspace 产物引用
- **THEN** 入口依据权威存储归属拒绝引用，不仅比较 issuer 字符串；当前平台可解析既有 evidence 引用，未支持存储的归属保持未验证并拒绝发布

#### Scenario: 产物声明来自本次执行

- **WHEN** 真实适配器收到图输出或新工具回执中的显式产物声明
- **THEN** 同步与流式均传递候选供归属校验；请求提供的历史工具消息和 workflow input_data 不得冒充新输出声明

### Requirement: 入口契约经 PostgreSQL 端到端回归

上述失败信封、登记保留与产物映射行为 SHALL 在 PostgreSQL 上经实际 HTTP 入口端到端验证:平台与执行存储均切换到 PostgreSQL,执行完成后以新的数据库会话查询,并排除内存事件兜底,证明信封与投影真正来自持久存储。验收 SHALL 参数化于 PostgreSQL 连接配置;未配置时显式报告 skip,不得视为通过。ASGI 层真实 HTTP 调用满足本项;真实 TCP 中途断连属于 G3 范畴,不在本项内。

#### Scenario: PostgreSQL 上成功与失败均可追溯

- **WHEN** 在配置了 PostgreSQL 的环境中经实际入口执行成功、拒绝、异常与停止四类请求,并以新会话查询事件与投影
- **THEN** 四类结果的信封、状态与错误类别均从 PostgreSQL 持久存储读取,与执行时一致,失败未伪报成功,响应不泄漏内部字段

#### Scenario: 未配置 PostgreSQL 时诚实跳过

- **WHEN** 运行入口契约端到端回归而未提供 PostgreSQL 连接配置
- **THEN** PostgreSQL 参数化验收显式报告 skip 并保留 SQLite 回归结果,不以 skip 冒充通过
