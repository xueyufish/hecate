# Design

## Context

单回合执行栈已交付:`DecisionStep`(先查后问 + 同事务提交)、`PortDecisionResponder`(冻结快照 → 结构化模型调用 → 动作提案)、`AcceptedActionStep`/`AcceptedActionBatch`(账本化串行派发)与 `AgentTurn`(固定顺序组合)。当前两类硬编码把决策空间限死为动作决策:

- `validate_decision`(`packages/hecate-runtime/src/hecate_runtime/accepted_decision.py`)钉死 `schema_version == 1` 且动作元组非空;
- `PortDecisionResponder.respond`(`port_decision_responder.py`)在工具快照为空或最终聚合无工具调用时抛 `DecisionValidationError`。

持久化侧已就绪:`durable_accepted_decision.schema_version` 为既有整型列(默认 1,无 CHECK 约束),SQL/InMemory 双实现共用同一参数化契约套件,内容摘要 payload 已含 `schema_version`。现有端口响应方写入 `{"text": ...}`；但 v1 `accepted_output` 的契约仍是任意合法 JSON 对象，不能假设全部历史记录采用该形状。

## Goals / Non-Goals

**Goals:**

- 单回合获得第二个出口:接受、持久化并恢复"不调用工具的最终回答"。
- 版本语义显式:v1/v2 校验规则与判别符收口在共享校验路径,未知版本 fail-closed。
- 历史零影响:v1 记录、摘要计算与全部既有测试行为不变。

**Non-Goals:**

- 不实现多轮 Controller、继续位置、等待/唤醒或完成控制;不判定任务成功。
- 不新增表、不做 schema 迁移、不回填历史记录。
- 不改公开平台 API、Task 生命周期、入口路由、前端或共享 fixtures。
- 不给最终回答增加独立的"完成事实"语义(那是后续 Controller/验收切片的职责)。

## Decisions

### D1:`schema_version=2` 作为唯一判别符

最终回答决策由记录级 `schema_version=2` 标识,配合"空动作集合 + `accepted_output.text` 非空白"校验;不在 `accepted_output` 内引入 kind 字段。

- 理由:v1 记录的 `accepted_output` 已含 `text` 键,任何"看内容猜类型"的方案都与"不重新解释历史 accepted_output"冲突;版本号在摘要 payload 内,v1/v2 摘要不可能碰撞,冲突仲裁(相同身份 + 不同摘要 → 显式冲突)天然覆盖跨版本改写。
- 备选:`accepted_output.kind` 判别——被否决,理由如上;`(run, step)` 之外另建判别列——被否决,无消费需求且违反"不新增表/列"的最小变更。

### D2:提案层显式 `kind`,决策组件映射到记录版本

`DecisionProposal` 增加显式判别字段(如 `kind: str = "actions"`,合法值 `actions`/`final_answer`),`DecisionStep` 据此写入 `schema_version`(actions→1、final_answer→2),并在提交前校验一致性:`final_answer` ⇒ 空动作集合且文本非空白;`actions` ⇒ 非空动作集合。`PortDecisionResponder` 按聚合结果显式设置 `kind`。

- 备选:`DecisionStep` 从动作集合是否为空隐式推断版本——被否决:静默推断把分类耦合到内容形状,宿主/响应方意图不可审计,且默认值路径会掩盖响应方实现错误(缺省 kind 的空动作提案在 v1 校验下显式失败,fail-closed)。
- 兼容性:字段带默认值,既有测试 stub 的 `DecisionResponder` 实现无需修改;空动作 + 缺省 `actions` 的提案被 v1 校验拒绝,不产生误接受。

### D3:最终回答文本复用 `accepted_output.text`

v2 记录要求 `accepted_output` 含非空白字符串 `text` 键(与 v1 responder 现行写入键一致)。这是 v2 版本语义下的新定义,不是对历史字段的重新解释;其他键保持合法 JSON 即可,摘要覆盖全部内容。

### D4:响应方流聚合——末块优先 + 消失保护

保留现行"任一带 `tool_calls` 的 chunk 覆盖前一聚合"的末块聚合语义,新增跨块 `saw_tool_calls` 标志:流中任一块出现过工具调用而最终聚合无工具调用 → `DecisionValidationError`(vanish 保护)。显式空列表可覆盖先前非空聚合，因此消失保护在当前路径可达。只有非空列表计入“出现过调用”；从未出现非空工具调用时，`tool_calls=[]` 与无工具调用等价，允许非空白文本最终回答；出现非空调用后再被清空必须失败。`tool_calls=None` 仅表示该块不更新累计列表，不能清除先前调用。用量回调仅在完整合法响应(含合法最终回答)时透传一次,沿用现行为。

### D5:空工具快照仅放行最终回答路径

移除 `respond()` 入口对空快照的无条件拒绝;空快照下模型仍可调用一次,聚合出现任何工具调用必然走 `_action` 的"快照外工具名"显式失败(快照为空时所有工具名都在快照外),最终回答路径不受影响。构造期校验(快照键与函数名一致、config 工具冲突拒绝)不变。

### D6:回合出口用派生只读属性显式化

`AgentTurnOutcome` 增加由已提交记录派生的只读判别(如 `is_final_answer` 属性,`record.schema_version == 2`),不改冻结数据类的构造签名——既有构造调用与解包不受影响,判别值与记录单一真源绑定、不可能漂移。最终回答回合经 `AcceptedActionBatch` 对空动作集合的既有空循环自然零派发,批处理与动作阶段代码无需分叉。

### D7:存储与事件零迁移

`schema_version` 列已存在,SQL/InMemory 实现经共享 `validate_decision` 路径自动获得 v2 校验;`_row_to_record` 恢复路径同理。`agent_decision_accepted` 事件 payload 的 `action_keys` 对 v2 为空列表(增量事实),`schema_version` 已在 payload 中。既有参数化契约套件扩展 v2 用例,双实现同套件运行。

## Risks / Trade-offs

- [v2 出口被上层误读为任务完成] → spec 显式禁止;回合结果判别只标识"已接受的模型输出";新增断言 Task 状态不变、无完成性字段的测试。
- [流聚合语义未来变更引入新的消失形态] → D4 消失保护以规格 + 测试固定(模拟"先工具调用后纯文本"的流形状),不依赖当前实现的覆盖语义。
- [v2 行被旧代码读到] → 旧代码(本变更前)对未知版本 fail-closed,不会误派发;回退仅使 v2 行不可读,不产生错误事实。
- [双实现契约漂移] → v2 用例进入参数化契约套件,SQL 与 InMemory 同套件验证;PostgreSQL 另有跨进程恢复用例。

## Migration Plan

纯增量版本引入,无数据迁移:部署新代码后新决策可为 v2,历史 v1 行原样可读可恢复。回退=代码回滚:v2 行对旧代码读取显式失败(fail-closed),不被派发、不被解释;不执行破坏性 downgrade。

## Open Questions

(无)
