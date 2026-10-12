# Design

## Context

见 proposal.md 的动机。当前代码事实(来自本 change 的只读调研):

- 直接循环(`chat.py _chat_with_tools`)用宽泛 `try/except Exception` 包装派发:`OutcomeUnknownError` 被字符串化成 `is_error` 工具消息回灌模型,循环继续;`BudgetRefusedError`(`GovernedModelService` 抛出)同样未被专项捕获;`max_iterations=5` 是硬编码步数上限,与计量预算互不相干。
- 回放链路存在但身份不稳定:`GovernedToolRegistry.completed_result` 在 claims 已配置时可用,但聊天侧 `invocation_id = chat:{session}:{tc.id}` 里的 `tc.id` 是模型当轮生成的调用号——重启/续跑后模型重新发起同一逻辑调用会拿到新 id,回放必然 miss。
- 引擎路径 `ToolWorker` 走 `ActionLedgerHook + EventStore + recovery_decision` 表,未知结果以 `RECEIPT_UNKNOWN` 事件与恢复裁决处理,语义正确;但它不经 `GovernedToolRegistry`,两条路径的治理入口不同。
- `WorkflowChildTaskAdapter` 的步骤仅有 `goal: str`;子回调 `_issue_child_callback` 无按步骤的幂等记录;父推进只读子终态,无产物校验。
- 场景包只有单轮静态 responder(`ticket_loop_responder`);tier 2 基线仅有检索单 Agent 基线(`single_agent_baseline.json`,成本门禁 not-collected),无真实模型多轮自主执行证据。
- runner `ModelNodeWorker` 为固定工具计划;是否模型驱动由本 change 的 ADR 决定,实施不在本切片。

## Goals / Non-Goals

**Goals:**

- 直接循环获得与引擎路径同等的"未知即停、预算即停、重放先于派发、边界取消、结构化输出"语义,并以同一任务在两条路径上可验证一致。
- 确定性 Workflow 安全调用自主 Agent 子任务:产物校验、失败路由、回调幂等。
- 产出四机制差异矩阵与证据驱动的 ADR;产出真实模型多轮执行的记录式基线(CI 不执行)。

**Non-Goals:**

- 不改变 G3 默认路由(直接循环仍是回滚路径);不实施 Runner 模型驱动选工具(ADR 结论另行立项);不补 step5d 剩余项(事件持久化入契约、定时执行器收口、A2A 身份);不新增受管认证范围。

## Decisions

### D1: 停止语义在循环边界用显式异常分类实现

`_execute_one` 的宽泛 except 之前增加专项分支:捕获 `OutcomeUnknownError` 与 duck-typed `outcome_unknown=True` 异常 → 抛出循环级终止信号(带动作关联 id),`_chat_with_tools`/流式变体以 `finish_reason="needs_review"` 类收尾;捕获 `BudgetRefusedError` → `budget_exhausted` 收尾;迭代上限保持独立计数、独立理由(新增枚举值,不与预算混用)。取消:每轮模型调用前检查请求断连/取消令牌,命中则以 `cancelled` 收尾。结束理由在响应与事件中均可见。引擎路径预期能满足同等对外行为;以同一场景断言验证,仅当证实缺口才做最小修正(预期 `RECEIPT_UNKNOWN` + 恢复表已覆盖,不动其内部结构)。

### D2: 回放身份改为"会话 + 轮次 + 内容"寻址

直接循环的逻辑调用身份从 `chat:{session}:{tc.id}`(模型生成、跨重启不稳定)改为 `chat:{session}:{turn_index}:{tool_name}:{args_digest[:16]}`:轮次序号取会话历史中已有 assistant 工具调用消息数(历史持久 ⇒ 跨重启稳定),内容摘要取规范化参数(与 step7 claim 键一致)。同轮重试命中同键 → 幂等回放;重启后续跑同轮同参 → 回放真实结果不重做;不同参数自然分键。重复同参有意写入属另一逻辑调用,由轮次序号区分——不会把"第二次有意写入"错误回放为第一次的结果。`tc.id` 仅作为展示关联,不再参与身份。

### D3: 结构化输出在请求层声明、返回前校验、单次有界重试

`ChatCompletionRequest` 增加可选 `output_schema`(JSON Schema);直接循环在产出最终回答时解析并校验,失败则注入校验错误反馈做**恰好一次**重试,再失败以 `invalid_output` 理由失败并携带首个错误摘要(不返回未过检内容)。重试仍是计量模型调用(预算自然覆盖)。引擎路径经入口服务完成时做同等校验(同一字段、同一单次重试)。未声明时零行为变化。

### D4: Workflow 子步骤 schema 扩展与回调幂等落在适配器与派发器

`WorkflowPlan` 步骤增加可选 `mode`、`output_schema`、`artifact`(期望产物键);`plan()` 做类型校验并显式拒绝非法值;`goal`-only 语义不变。子终态回调处理处(`_issue_child_callback` → 父推进)在推进前校验:产物存在且输出过 schema 才算成功,否则按 `on_failure` 路由并标记失败来源类别(`execution` vs `validation`)。幂等:在父任务持久 extra 中记录 `_workflow_callbacks[step_index] = {child_task_id, applied_fact_id}`,应用与记录同事务提交;重复/迟到回调对照记录拒绝并回执,不二次推进。持久等待/唤醒语义复用 step6,不重定义。

### D5: 差异矩阵为受维护设计文档

新建 `docs/design/execution-mechanism-matrix.md`:四机制 × 八轴(模型调用、工具选择/结果回灌、结构化输出、预算/停止、审批等待、持久化、取消、错误事件),每格链接证据(代码位、spec、测试);文档头部声明维护约定:任一机制语义变化需同步更新对应行。作为 ADR 与后续 I-Ca/M-S 验收的对照物,不做机器强制(评审把关)。

### D6: ADR 证据先行、结论后置

新增 ADR(沿用 docs/design/adr/ 编号序列),比较三个方案:A. 复用现有 Pregel 图机制(聊天引擎路径为默认收敛目标);B. 收敛直接循环为默认并降级图路径;C. 抽取轻量 harness 供聊天与 runner 共用。每方案按"必要改动、依赖、恢复复杂度、维护成本、与治理边界的关系"评估,证据来源 = 差异矩阵 + 本 change 两条路径的真实模型运行报告。初稿允许保留"待证据"结论;**禁止**在证据齐备前写入拆包/移除 Pregel/新设 step5f 的结论。Runner 的模型驱动问题在同一 ADR 内作为子决策记录。

### D7: 真实模型验证 = 确定性 CI 场景 + 记录式基线,双轨分离

- 确定性轨(tier 1,CI 收集):新增反馈驱动脚本 responder——按上一工具结果内容决定后续工具(读材料 → 依据反馈选工具 → 结构化收尾),覆盖多轮;同一 responder 同时驱动两条聊天路径的场景测试,断言停止语义/重放/结构化输出的一致性。
- 真实模型轨(tier 2,CI 永不收集):扩展 `tests/scenarios/baseline/run_baseline.py` 支持同一任务定义,产出记录式 JSON(扩展既有 baseline schema):固定模型与工具版本、逐轮工具选择、rubric 分(工具选择正确性、结构化输出合格、未知停止合规、预算合规)、延迟与成本总额及阈值判定;报告落 `tests/scenarios/baselines/`,缺真实模型密钥时显式跳过。
- manifest 登记新场景(tier 1 翻转 implemented;真实模型轨以基线文件存在性登记,不伪称 CI 门禁)。

## Risks / Trade-offs

- [轮次寻址在历史被裁剪/压缩后失稳] → 轮次序号取自持久会话历史的 assistant 工具调用消息计数;压缩若移除该类消息,回放退化为未命中并走正常派发(step7 claim 冲突语义兜底),不产生错误回放。
- [结构化输出重试增加延迟与成本] → 单次重试上限;重试计入预算;未声明 schema 零开销。
- [两条路径治理入口不同(循环走 GovernedRegistry、引擎走 ToolWorker)长期漂移] → 差异矩阵显式登记该差异;parity 场景双路径断言;统一入口的取舍交给 ADR,不在本 change 强行合并。
- [子回调幂等记录与状态推进的事务边界] → 记录与推进同事务(复用 task state CAS);崩溃窗口由既有 step6 恢复裁决兜底。
- [真实模型报告不可复现] → 固定模型/工具版本与数据;报告记录原始配置与时间戳;阈值判定独立字段,不冒充确定性断言。

## Migration Plan

1. 差异矩阵初稿 + ADR 骨架(证据列空)。
2. 直接循环停止语义与取消(新增结束理由枚举;旧客户端未见新字段时行为不变)。
3. 回放身份切换(D2 键格式变更仅影响新写入的 claim 键;旧格式键在回放查询时兼容读取,不做数据迁移)。
4. 结构化输出字段(可选、additive;入口层校验兼容旧请求)。
5. Workflow 子任务 schema/校验/幂等(旧 `goal`-only 负载等价受理)。
6. 确定性场景 + 真实模型基线;用证据回填 ADR 与矩阵。
7. 回退 = 按 commit 粒度 revert;不触碰 step6 不变量与 G3 默认值。

## Open Questions

- 无阻塞性问题:ADR 结论、Runner 子决策均按设计**有意**后置到证据齐备;若真实模型轨因密钥不可用长期缺位,ADR 以确定性双路径证据 + 矩阵先行给出带"真实模型证据待补"限定的结论,并显式登记该限制。


## 复核修正

- D1 的引擎行为假设被双路径真实入口测试否定：未知回执记录后仍会回灌错误。
  ToolWorker 现在落账后抛出 kernel-owned `ExecutionStoppedError`；终止错误不重试，
  已派发的并行工具全部结束后才传播，HTTP/SSE 映射共享类别。
- D2 在原有身份后增加工具调用前历史摘要；没有 session 时分配本次独立命名空间。
  仅按工具消息数量计数会在裁剪历史后碰撞旧回执，不能依赖不同 claim key 自动冲突。
  持久历史末尾存在未回填工具调用时，先以其历史前缀和序号回放/派发再继续模型。
  不认证任意裁剪历史后的受保护副作用恢复。
- D3 的最终输出重试只调用模型且禁止工具；重试仍检查预算和取消。声明 schema 的流式
  请求由共同入口延迟返回内容，失败发送 SSE error 和 `[DONE]`。Agent 路由和普通模型
  路由均验证字段；不重新执行图来修复格式。
- D4 步骤字段持久传入 `workflow_step`；子任务键按父任务和步骤固定。产物可从子任务
  已提交的结构化输出提取，父流程仍负责验收。回调结果以子任务持久事实为准，不能由
  caller summary 补造；步骤与父子关系也取持久记录。`_workflow_callbacks` 与消费等待
  token/父任务 requeue 同事务提交。
- D7 记录轨比较实际 DL 和 PG 入口，覆盖 normal/unknown/budget/cancel 注入；记录全部
  工具选择、输入摘要、版本、用量和阈值。无密钥生成 skipped 记录，成本未知保留 null；
  真实模型审批恢复、Runner 自主选择及业务收益仍未验证。报告生成器的注入 provider
  测试仅验证格式与执行链，不作为真实模型报告。
