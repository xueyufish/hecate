# platform-entry-execution Specification

## Purpose

平台入口执行应用服务:被迁移的入口调用链(HTTP chat、MCP `agent_chat`/`session_resume`、IM 注入、评估 workflow 执行)经同一个平台入口执行服务发起执行;入口模块只保留协议适配(身份校验、请求/响应格式、流式协议),执行装配(含事件存储与 checkpoint)发生在入口服务内,平台 adapter 与独立宿主共享同一装配。同步与流式接口是同一次执行上的等待/订阅视图,不引入独立执行生命周期。

## Requirements

### Requirement: 平台入口统一消费一个执行应用服务

被迁移的入口调用链(HTTP chat、MCP `agent_chat`/`session_resume`、IM 注入、评估 workflow 执行、A2A executor、定时任务 agent executor)SHALL 经同一个平台入口执行服务发起执行;入口模块只保留协议适配(身份校验、请求/响应格式、流式协议),MUST NOT 内联构造执行服务或自带装配。入口服务经平台 adapter 消费共享装配;装配一致性以实例为单位界定:单进程内引擎事件存储 SHALL 只有一个共享实例(由应用生命周期装配注册,入口只读消费),入口模块 MUST NOT 自建存储单例、MUST NOT 依赖与其他入口语义耦合的私有装配函数——工具注册/加载等入口侧共享装配 SHALL 来自唯一的公开装配来源。同步与流式接口是同一次执行上的等待/订阅视图,MUST NOT 引入独立执行生命周期。已迁移入口模块 MUST NOT 保留绕过执行服务的直连模型调用;仍走非统一装配的路径(如定时任务 workflow 经 studio 测试入口)MUST 以显式登记的绕过路径记录,不得与已迁移入口混淆。定时任务调度器触发 SHALL 经 executor registry 将执行分派到任务目标对应的执行器并如实记录执行结果;不执行实际工作、直接记为成功的空转路径 MUST NOT 存在。

#### Scenario: MCP 入口装配与 HTTP 一致

- **WHEN** 同一配置工具的 agent 分别经 HTTP chat 与 MCP `agent_chat` 执行
- **THEN** 两者的事件日志均含配对的 `TOOL_CALL`/`TOOL_RESULT`,MCP 执行不再缺少事件与 commit point 产出

#### Scenario: 跨入口共享同一事件存储实例

- **WHEN** 一次执行经任一已迁移入口(如 MCP)写入引擎事件后,从进程内另一入口使用的存储读取同一会话/run 的事件
- **THEN** 事件可读且内容一致;不存在按入口分裂的多个存储实例(进程内后端下尤其不得互相不可见)

#### Scenario: 入口模块不得内联装配执行服务

- **WHEN** 检查被迁移入口模块的 import
- **THEN** 不存在对执行服务的直接构造调用、自建的事件存储单例、或对其他入口模块私有装配函数的导入,执行与装配仅经入口服务和公开装配来源发起

#### Scenario: 尾链迁移后无直连模型绕过

- **WHEN** 检查 A2A executor 与定时任务 agent executor 的执行路径
- **THEN** 不存在绕过入口服务的 `llm_service` 直连调用,两条链与 HTTP 入口共享同一装配语义(工具面、guardrail、事件/checkpoint)

#### Scenario: 未迁移链显式登记

- **WHEN** 查看仍经 studio 测试入口执行的定时任务 workflow 路径
- **THEN** 该路径绕过入口服务的现状有登记记录,不与已迁移入口混淆

#### Scenario: 调度触发真实分派

- **WHEN** 定时任务调度器因 cron 触发执行一个已启用的任务
- **THEN** 执行经 executor registry 分派到该任务目标(agent/workflow)对应的执行器,执行记录反映执行器的真实结果而非无条件成功

### Requirement: 入口执行关联统一 Task/Run

业务入口(HTTP/MCP/IM/A2A)与定时任务 agent 入口经入口服务的每次执行 SHALL 在 TaskRunRegistry 登记 Task(发起者身份链、workspace 归属)与 Run(尝试、身份链快照),会话型执行并登记 conversation 链接;同一 agent 的同一请求经不同入口执行时,可按 agent/会话追溯到关联的 Task/Run。A2A 与定时任务执行的 workspace 归属 SHALL 取执行所用 agent 行的 workspace,不得无 workspace 归属。评估等平台内部批量入口 SHALL 将其执行关联到单一内部 Task,而非逐条目新建 Task。登记 MUST NOT 改变执行行为;登记失败时执行结果中显式标记关联缺失,MUST NOT 静默宣称已登记。执行完成或失败后,已登记的 Task/Run 投影 SHALL 反映终态:成功执行携带结果摘要(最终内容摘要/声明的产物引用),失败执行携带停止类别与安全摘要(映射到平台任务生命周期的既有终态词汇,不新增状态语义);执行内部异常细节 MUST 保留在服务端日志,契约面只承载类别与关联引用。流式与非流式对终态与关联信息的呈现 SHALL 对称:两种形状下客户端均可取得 Task/Run 引用,关联缺失时均可显式观察。

#### Scenario: 跨入口追溯同一 Task/Run 关联

- **WHEN** 同一 agent 分别经 HTTP 与 MCP 入口执行并查询其会话的关联记录
- **THEN** 两次执行均可解析到登记的 Task 与 Run,身份链与 workspace 归属可查

#### Scenario: A2A 与定时任务执行关联 Task

- **WHEN** A2A SendMessage 或定时 agent 触发经入口服务完成执行
- **THEN** 该执行按所选 agent 的 workspace/agent 归属登记 Task/Run,可按 agent 追溯

#### Scenario: 评估执行关联单一内部 Task

- **WHEN** 一次评估运行触发多条 workflow 执行
- **THEN** 这些执行关联到该评估的单个内部 Task,不逐条目创建独立 Task

#### Scenario: 登记失败显式可见

- **WHEN** Task/Run 登记未能完成而执行继续
- **THEN** 执行结果或日志显式标记关联缺失,不以已登记状态上报

#### Scenario: 失败执行回写终态与类别

- **WHEN** 经入口服务的执行以停止类别(如未知结果/预算耗尽/取消)或内部异常终止,且 Task/Run 已登记
- **THEN** 关联的 Task 投影反映失败终态并携带该停止类别;响应或事件中的错误契约面只含类别、安全摘要与关联引用,内部异常细节不出现在契约面

#### Scenario: 成功执行回写结果摘要

- **WHEN** 经入口服务的执行成功完成且 Task/Run 已登记
- **THEN** 关联的 Task 投影反映成功终态并携带结果摘要,可按 Task 查询到最终输出的契约级摘要

#### Scenario: 流式与非流式关联信息对称

- **WHEN** 同一入口分别以流式与非流式执行并产生终止(正常或停止)
- **THEN** 两种形状的客户端可见输出均可取得本次执行的 Task/Run 引用(或显式的关联缺失标记),不因形状不同而丢失关联可见性
### Requirement: 引擎事件映射到平台契约

入口服务 SHALL 将引擎原始流事件映射为 `contracts/execution` 的 `EventEnvelope`(task_ref/run_ref、source_sequence、payload schema 引用),并支持按 run 引用与游标的分页读取;原始引擎事件 MUST 作为后端详情保留,不因映射而丢弃。tool call/result 的配对在映射层校验,未配对事件 MUST 显式暴露(校验失败或告警),不得静默吞掉。客户端可见的 OpenAI 风格响应与 SSE chunk 由适配层转换产生,MUST NOT 携带 backend 专属字段(引擎内部路由/状态通道名等)。经平台任务控制面派发的执行,其映射后的事件 SHALL 持久追加到平台事件存储,分页读取从持久存储出发:跨请求与进程内重连后按游标恢复,不依赖进程内内存保留;内存保留仅可用于未经任务控制面派发的执行。非控制面派发的入口执行(聊天直连等)在装配了持久事件存储时,其映射后的信封 SHALL 同样持久追加(以 run 作用域键),内存后端装配时允许进程内保留;分页读取先查事件存储,仅对未持久化的执行回退进程内保留。同步(非流式)执行 SHALL 将其已排空的引擎事件经同一映射器产生信封并遵循同一持久化规则,使同步与流式对同一 Task/Run 形成可分页的一致视图。

#### Scenario: 事件可按 run 与游标读取

- **WHEN** 一次执行完成后按 run 引用读取事件页并携带上次游标
- **THEN** 返回的 envelope 序列连续、可翻页,且包含 tool 事件的配对语义

#### Scenario: 原始事件作为后端详情保留

- **WHEN** 读取映射后的事件
- **THEN** 每个可映射事件的后端详情中可取得原始引擎事件

#### Scenario: 客户端 chunk 不含 backend 专属字段

- **WHEN** 检查流式响应的 SSE chunk 字段集合
- **THEN** 字段限于 OpenAI chunk 语义,引擎内部状态字段不出现在客户端可见输出中

#### Scenario: 任务控制面事件持久可恢复

- **WHEN** 经任务控制面派发的执行产生流事件后,以新的读取请求(非原请求上下文)按 run 引用从游标 0 读取
- **THEN** 事件从持久存储返回,与执行时映射的 envelope 一致,不因原请求结束而丢失

#### Scenario: 聊天执行信封随持久后端落库

- **WHEN** 聊天入口执行在装配了持久事件存储的部署上完成(流式或非流式)
- **THEN** 映射后的平台信封持久追加到事件存储,可按 run 引用从存储分页读取,不依赖原请求进程内存

#### Scenario: 同步与流式同一视图

- **WHEN** 同一入口分别以非流式与流式执行同类请求并按各自 run 引用读取事件页
- **THEN** 两种形状产生的信封经同一映射与持久化路径可分页读取,视图语义一致(配对校验、游标、后端详情保留)

### Requirement: 入口层禁止新增引擎具体导入

被迁移的入口层模块(channel/api、tools/mcp、channel/im、channel/a2a/server)MUST NOT 直接 import 引擎具体实现类(`PregelRuntime`、`GraphCompiler`);执行装配只发生在入口服务与共享装配内。该约束 SHALL 由分层测试自动化执行,违规时测试失败并指出模块与符号。

#### Scenario: 分层测试拦截具体导入

- **WHEN** 入口层模块(含 channel/a2a/server)新增对 `PregelRuntime` 或 `GraphCompiler` 的 import
- **THEN** 分层测试失败并指出违规模块与符号

### Requirement: 执行包与宿主的升级支持窗口显式

hecate-runtime(内置执行包)、hecate-runner(宿主)与平台主体的组合升级支持窗口 SHALL 以显式契约/依赖矩阵成文(兼容组合、边界版本、退出条件),并被 CI 断言与 lock/workspace 实际版本一致;矩阵外组合 MUST NOT 被静默当作受支持组合。

#### Scenario: 矩阵与实际版本一致

- **WHEN** CI 校验契约/依赖矩阵
- **THEN** 矩阵声明的组合与 workspace/lock 中的实际版本边界一致

#### Scenario: 矩阵外组合不被宣称支持

- **WHEN** 组合落在矩阵声明的边界之外
- **THEN** 文档与矩阵不将其列为受支持组合,文档中的支持窗口与矩阵一一对应

### Requirement: 每次入口迁移附真实入口回归

每一组入口链的迁移 SHALL 附带经真实入口(不 mock 执行服务本身)的回归:流式与非流式、多轮工具调用、审批拒绝路径、会话中断后恢复(断线/恢复)路径,以及该入口特有的失败模式;迁移前该入口的可观察行为(响应 schema、SSE 格式、finish_reason、审批记录)在迁移后保持兼容。部分入口未迁移时只记录该切片完成,MUST NOT 勾选整个入口迁移项。

#### Scenario: 真实入口回归覆盖双模式

- **WHEN** 运行被迁移入口的回归套件
- **THEN** 流式与非流式请求均经真实协议入口执行并通过,响应格式与迁移前基准一致

#### Scenario: 会话中断后恢复有真实入口证据

- **WHEN** 同一会话的执行在入口断开后以同一会话标识再次经真实入口发起
- **THEN** 恢复执行的测试证明状态/事件连续性与 tool call/result 配对,不因入口切换或断线静默丢失

#### Scenario: A2A 协议响应保持兼容

- **WHEN** A2A SendMessage 经迁移后的入口执行并返回 Task
- **THEN** Task/Artifact 字段集合与状态枚举与迁移前基准一致,A2A 客户端无需变更

#### Scenario: 切片完成不被夸大

- **WHEN** 定时任务 workflow 入口仍经 studio 测试入口执行
- **THEN** 记录只声明已完成切片,入口迁移整体项保持未勾选

### Requirement: 入口关联原子性与真实主体

入口服务 MUST 按 agent/workspace 解析真实 AgentPrincipal,MUST NOT 把 Agent ID 当作 Principal ID。关联失败 MUST 使用 savepoint 回滚本次关联,不污染调用方已有事务,不返回内部 SQL/错误详情;实际执行 session 与关联 session MUST 一致。内部 workflow 评估 MUST 使用真实 runtime factory,传递 workspace、固定 workflow 版本、共享存储和 guardrail,并在持久 trajectory 中保留关联结果。

#### Scenario: 真实 Principal 与 Agent ID 不同

- **WHEN** 使用正常注册的 Agent 执行,Principal ID 与 Agent ID 不同
- **THEN** Run 的身份快照指向真实 Principal,关联成功

#### Scenario: Run 登记 flush 失败

- **WHEN** Task 创建后 Run 的 flush 失败
- **THEN** 本次关联回滚且返回安全 missing 原因,已有事务仍可使用,不保留孤立 Task

### Requirement: 共享装配保留引擎提交日志与工具作用域

共享执行装配 MUST 把注入的事件存储同时用于引擎和 Worker,生成 TURN、CHANNEL_WRITE 与 STEP_END,不得只写工具事件并声称有完整提交日志。共享工具装配 MUST 按 workspace 加载和执行工具;其他 workspace 的同名工具 MUST NOT 参与当前 Agent 的执行。

#### Scenario: 通过共享装配执行一次完整 Run

- **WHEN** 使用配置事件存储的 builtin 或平台执行路径完成一次执行
- **THEN** 同一 session 可读取 TURN_START/TURN_END、CHANNEL_WRITE 和 STEP_END

### Requirement: 入口安装/升级支持矩阵

入口(HTTP、MCP、A2A、IM、定时 agent、定时 workflow、评测、studio 测试)在各消费模式(平台内置安装、独立宿主、受管宿主)下的安装与升级支持性 SHALL 以显式矩阵成文:每个入口×模式单元格声明受支持/不受支持/未认证,并指向可验证证据(CI 作业或测试);未覆盖或未认证的组合 MUST 显式登记为缺口,不得默认当作受支持。矩阵 SHALL 与执行包/宿主的版本窗口矩阵交叉引用,两者变更时同步更新。

#### Scenario: 矩阵单元格可追溯到证据

- **WHEN** 查看入口支持矩阵中声明为受支持的单元格
- **THEN** 该单元格指向对应的 CI 作业或测试证据,证据实际存在且覆盖该入口×模式组合

#### Scenario: 缺口显式登记

- **WHEN** 某入口在某消费模式下缺少安装/升级证据
- **THEN** 矩阵将该单元格登记为不受支持或未认证并命名缺口,不将其列为受支持

### Requirement: 最终输出决定入口终态

入口 SHALL 在最终输出校验及有限修复成功后才发布成功投影与摘要;校验终止 SHALL 携带停止类别及真实关联。流式完成 SHALL 保存最终内容的有界摘要。流式与非流式停止及结构化输出 SHALL 保留相同的关联可见性,包括缺关联的显式标记。

#### Scenario: 最终校验拒绝

- **WHEN** 引擎已生成内容,但声明的输出 schema 经允许的修复后仍不满足
- **THEN** 客户端收到 invalid_output 与真实关联,Run 投影为 failed,不能保留 succeeded

#### Scenario: 流式成功摘要

- **WHEN** 已登记 Run 的流式执行正常耗尽
- **THEN** Run 投影为 succeeded 并包含最终内容摘要

#### Scenario: 非流式停止转换

- **WHEN** 已登记 Run 的执行以预算、取消、迭代上限或其他停止类别返回非流式响应
- **THEN** 终止载荷包含相同 Task/Run 引用,异常转换不丢失引用

### Requirement: 关联缺口与内部事件隔离

缺少 Run 的业务入口 SHALL 明确标记关联缺失并保留已实际登记的 Task 引用;内部 engine 事件集合 MUST NOT 进入返回结果,即使登记失败或仅登记 Task。平台信封的持久化 SHALL 由共享入口执行边界承担,不依赖特定协议消费者再次写入。

#### Scenario: 缺关联的同步输出

- **WHEN** 同步执行没有完整 Task/Run 关联
- **THEN** 结果保持业务响应,内部 engine 事件集合被剥离,关联缺口明确

#### Scenario: 非 HTTP 流式消费

- **WHEN** 任意共享入口消费者排空已登记 Run 的流式事件
- **THEN** 新读取上下文能按 Run 读取持久信封,HTTP 渲染不重复追加

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
