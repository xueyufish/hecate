# step5d-contract-closure 实现复核记录

## 交付范围

- **终态回写**:`TaskRunRegistry.finalize_run`(成功摘要/停止类别/取消,走 `update_projection` 单写者通道,状态词复用 dispatcher 已读取的投影终态词汇);`EntryExecutionService` 重排为**登记先于执行**——失败执行留下已登记的 Task/Run 并回写失败终态,不再凭空消失;停止类别映射(`ExecutionStoppedError.reason_category`、`MaxSuperstepsError→iteration_limit`、`BudgetRefusedError→budget_exhausted`、其余→`execution_failed`);流式经生成器包装在耗尽/异常时回写;内部异常细节只入服务端日志,契约面承载类别+引用+稳定文案;注册的关联以 `platform_correlation` 属性随异常传递给协议适配层。
- **关联可见性对称**:聊天非流式恒附 `hecate_correlation`(registered 携带引用/missing 携带原因);流式首帧为 OpenAI 安全关联帧(`choices: []`,严格 chunk 消费者无感);停止 409/SSE 终止载荷携带 `correlation`。
- **信封持久化与同视图**:`RunEventMapper` 增加可选事件存储 sink(run 作用域确定性 UUID 键、CUSTOM 类型、信封 to_dict 落 payload);`read_events` 先查存储、回退进程内保留;新 mapper 实例跨请求可分页持久信封;`_non_stream_execute` 收集排空的引擎事件挂 `_engine_events`,入口服务经同一映射器落信封后剥离该键(协议输出零泄漏)。
- **定时 workflow 收口**:`WorkflowExecutor` 重写为 `EntryExecutionService` 路径(agent_mode=workflow、创建者 workspace 归属、result_summary 绑定不变、会话关闭前提交关联行);`WorkflowTestRunner` 收缩为 studio 测试端点专用,源码级断言钉住定时路径不再引用。
- **A2A 解析收口**:多 agent 作用域经**默认部署**解析(部署表为显式登记面);无唯一默认部署拒绝;单 agent 沿用;作用域外不可选与不泄露语义保持。
- **入口支持矩阵**:`docs/design/entry-support-matrix.md`(入口×消费模式×支持性+证据指针+诚实缺口),与执行栈兼容矩阵交叉引用;演进计划 step5d 两项剩余条目关闭。

## 验证

| 验证 | 结果 |
|---|---|
| 新增单测(test_entry_contract_closure) | 8 passed(成功/停止/generic/missing 终态回写、流式取消、信封跨 mapper 分页、内存回退) |
| 改写套件(scheduling executors、A2A 入口、G3 流式) | 全绿 |
| 宽回归(channel/execution/a2a/services/mcp/分层) | 2571 passed, 58 skipped, 0 failed |
| ruff check / format(src+tests) | 通过 |
| mypy src | 634 files 零错误 |

计数不跨运行相加;宽回归的 skip 为既有的 PostgreSQL 条件跳过。

## 实现中的取舍与发现

1. **登记先于执行**:原实现 correlate 在执行之后——失败执行无任何 Task/Run 可追溯。重排后语义更符合"关联统一"要求;流式路径原本就实质先注册,行为无回归。
2. **终态落点为 Run 投影**:TaskModel 无状态列,dispatcher 的 `_RUN_PROJECTION_TERMINAL` 已从 `run.projection["state"]` 读终态——终态回写复用同一词汇,不新增状态语义。
3. **失败回写的持久性边界**:回写经 `begin_nested` 落在请求会话上;停止类失败以正常响应返回(会话照常提交,投影持久);内部异常导致 500 时外层回滚会连带丢弃登记与回写——与既有登记行的提交纪律一致,如实登记为边界。
4. **信封存储键**:事件存储 ABC 的 session 键为 UUID,run 作用域键用 `uuid5(NAMESPACE_URL, "run:{domain}:{id}")` 确定性派生,跨进程稳定且与 session 键空间不碰撞;不改 ABC 与 EventType。
5. **关联首帧兼容性**:流式关联帧携带 `choices: []`,严格 OpenAI chunk 消费者无感;g3 流式测试的 delta 抽取加了形状守卫。
6. **task_dispatcher 的控制面信封持久**(`PlatformEventService` 重排)保持原样——mapper 不接 sink,避免双写;聊天入口路径走 sink。

## 未验证项

- Postgres 事件存储下的信封分页(条件跳过沿用既有模式;内存后端已验证 sink/分页语义)。
- IM/评测入口的终态回写端到端(入口服务为共享路径,单测覆盖服务级行为;入口侧仅矩阵登记)。
- 独立/受管宿主面的定时调度能力(矩阵中登记为未认证缺口,不预设交付)。
- A2A 调用方→agent 的协议级映射(独立协议演进,维持登记缺口)。
- 分布式多进程下的信封分页一致性(以存储有序追加与单写者为界)。
