## Purpose

为 agent 执行提供统一的有序守卫中间件链：守卫按声明顺序组成 `next()` 委派链，在全部执行路径（Pregel workers 与直连工具循环）上以一致的拦截点、短路语义和数据传递规则运行，取代每个拦截点仅容纳单个 hook 的扁平槽位模型。

## ADDED Requirements

### Requirement: 守卫链按声明顺序执行
系统 SHALL 在每个拦截点维护一个有序的守卫 stage 列表，并按声明顺序依次执行。每个 stage 接收上游传递的数据（可能已被上游 SANITIZE 修改），执行检查后将数据传给下一个 stage。

#### Scenario: 顺序执行可观测
- **WHEN** 同一拦截点注册了 stage A（序号 1）与 stage B（序号 2）
- **THEN** A 先于 B 执行，B 接收到的输入是 A 处理后的数据

#### Scenario: 空链等价于放行
- **WHEN** 某拦截点的 stage 列表为空
- **THEN** 调用直接放行，数据不变

### Requirement: BLOCK 短路剩余 stage
任一 stage 返回 BLOCK 决定时，系统 SHALL 停止执行该拦截点的后续 stage，并将拒绝原因（含产生拒绝的 stage 标识）返回给调用方。守卫 SHALL 只能收紧（拒绝），不得在链内放宽其他 stage 的决定。

#### Scenario: 短路且携带来源
- **WHEN** stage A 返回 BLOCK（reason="injection detected"），stage B 尚未执行
- **THEN** B 不执行
- **AND** 返回的拒绝信息包含 reason 与 stage A 的标识

### Requirement: SANITIZE 传递修改后数据
任一 stage 返回 SANITIZE 时，系统 SHALL 将其 `modified_data` 作为下游 stage 的输入继续执行。当 SANITIZE 结果缺失 `modified_data` 时，系统 SHALL 视为该 stage 契约违例并按 BLOCK 处理（携带该 stage 标识与违例原因），不得静默降级为放行。

#### Scenario: 修改向下游传递
- **WHEN** stage A 返回 SANITIZE 且 modified_data 将 messages 中的敏感字段替换
- **AND** stage B 读取输入
- **THEN** B 看到的是替换后的 messages

#### Scenario: 缺失 modified_data 视为违例
- **WHEN** stage A 返回 SANITIZE 但 modified_data 为 None
- **THEN** 该拦截点按 BLOCK 处理，拒绝信息标明 stage A 违例
- **AND** 后续 stage 不执行

### Requirement: per-agent scope 过滤
守卫链 SHALL 支持按 agent 配置过滤生效的 stage 集合：未在 agent 配置中启用的 stage 对该 agent 的执行不生效。配置面为 agent 的 guardrail 配置（现有 `guardrail_config` 字段接活）。

#### Scenario: 未启用的 stage 不执行
- **WHEN** stage S 在 agent X 的 guardrail 配置中未启用
- **AND** agent X 的执行经过该拦截点
- **THEN** S 不参与该次执行

#### Scenario: 启用的 stage 正常执行
- **WHEN** stage S 在 agent Y 的 guardrail 配置中已启用
- **AND** agent Y 的执行经过该拦截点
- **THEN** S 参与该次执行并受链语义约束

### Requirement: 旧 hook ABC 适配为单 stage
现有的四类守卫 hook 抽象（PreLLMHook / PostLLMHook / PreToolHook / PostToolHook）SHALL 可通过适配器作为单个 stage 加入链中，适配器保留 hook 的 matcher 工具名过滤语义。旧的单 hook 构造参数保留兼容（内部包装为单 stage 链）。

#### Scenario: 旧实现无需改写即可入链
- **WHEN** 一个现有的 PreLLMHook 实现被包装为 stage 并加入 pre-request 拦截点
- **THEN** 其决策语义（ALLOW/BLOCK/SANITIZE）在链中保持不变

#### Scenario: matcher 过滤保留
- **WHEN** 一个带 matcher 的 PreToolHook 实现被包装为 stage
- **AND** 当前调用的工具名不匹配 matcher
- **THEN** 该 stage 直接放行（等效于不参与）

### Requirement: 链机制固定在引擎内核
链的组合与执行语义（stage 顺序、BLOCK 短路、SANITIZE 传递、单调收紧）SHALL 固化于引擎内核，不作为可插拔机制开放；可插拔的单元是 stage 本身。链组件 SHALL 位于 engine 层并满足 engine 层零外部依赖约束（仅标准库）。

#### Scenario: 内核语义不可被 stage 覆盖
- **WHEN** 任意 stage 实现（包括用户提供的实现）加入链
- **THEN** 其仍受链内核的顺序与短路语义约束，无法重排或跳过其他 stage

#### Scenario: engine 层零外部依赖
- **WHEN** 链组件所在模块的 import 被检查
- **THEN** 无 `hecate.models` / `hecate.services` / `hecate.api` 或第三方包引用

### Requirement: 全部执行路径统一接入守卫链
Pregel 路径的 LLM/工具 worker、agent 节点执行端口，以及直连工具循环（chat 路径 A）SHALL 调用同一守卫链组件完成各自拦截点的守卫检查，不得各自维护独立的守卫调用逻辑。

#### Scenario: Pregel 工具节点走链
- **WHEN** Pregel 路径执行一个 tool-call 节点
- **THEN** 其 pre-execute / post-execute 拦截点经过守卫链

#### Scenario: 直连工具循环走链
- **WHEN** chat 路径 A 的直连工具循环发起一次工具调用
- **THEN** 该调用经过与 Pregel 路径相同的门控与守卫链组件

#### Scenario: agent 节点 LLM 调用走链
- **WHEN** agent 节点（非 conversation 节点）发起 LLM 请求
- **THEN** 其请求拦截点经过守卫链，与 conversation 节点的守卫语义一致

### Requirement: stage 决策可审计
链在执行中 SHALL 产出每个 stage 的决策记录（stage 标识、决策、原因），供事件日志与安全审计消费。BLOCK 决策 MUST 有对应的审计记录。

#### Scenario: BLOCK 决策留痕
- **WHEN** 链中任一 stage 产生 BLOCK
- **THEN** 审计记录中可查到该 stage 标识、BLOCK 决策与原因
