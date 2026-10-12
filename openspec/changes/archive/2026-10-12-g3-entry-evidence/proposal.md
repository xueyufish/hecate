# Proposal: G3 实际入口证据补齐与默认启用决策(g3-entry-evidence)

## Why

演进计划 step5d 剩余项明确要求:"完成 G3 实际入口的多轮、拒绝、取消、断连/恢复及默认启用证据;复用现有开关和路由,旧循环限制须显式保留。"

现状盘点(`tests/test_channel/test_chat_engine_g3_entry.py`,其 `engine_path` fixture 为 autouse——全部测试在全局开关开启下经真实 HTTP 入口执行):

- **已有引擎路径真实入口证据**:多轮工具调用(流式/非流式)、审批拒绝呈现、流断连后状态持久与恢复、会话路径亲缘、Task/Run 关联。
- **缺口**:
  1. **取消**:该文件没有取消用例;step5e 的迭代边界取消检查(`cancel_check` 在每个模型边界生效)在引擎路径真实入口上没有证据。
  2. **跨请求多轮会话连续性**:现有多轮证据是单请求内的多轮工具环;"同一 session 多次请求、历史延续、亲缘保持、step5e 重放身份跨轮稳定"没有入口级证据。
  3. **关态钉住**:autouse fixture 强制开态,整个文件没有"开关关闭时直连循环行为不变"的真实入口用例;直连循环的显式限制(不经引擎事件日志、无持久回放保证)只在代码 TODO 注释里,没有形成文档与断言。
  4. **默认启用证据**:全局默认 `CHAT_TOOL_LOOP_ENGINE_ENABLED=False`。规格("引擎路径具备真实入口证据")要求逐入口积累证据、缺证据不切默认,但"证据齐备后如何决策"没有可查询的决策记录;确定性治理拒绝(与审批拒绝不同路径)的引擎入口证据也缺失。

## What Changes

1. **引擎路径取消证据**:真实 HTTP/SSE 入口、会话中途中断连接,断言迭代边界取消检查生效——进行中的工具轮恰好完成一次、取消以 step5e 终止类别呈现或可观察、会话状态持久、恢复不重做副作用。
2. **引擎路径跨请求多轮连续性证据**:同一 session 的第 2/3 轮请求,历史延续(模型看到前轮助手回答)、路径亲缘保持、前轮已决回执不重复执行(step5e 重放身份跨轮稳定)、tool 配对完整。
3. **关态钉住**:真实入口上开关关闭(直连循环)行为与迁移前一致;直连循环的显式限制落成文档(与 S12/机制矩阵交叉引用),并有测试断言关态请求不产生引擎事件日志。
4. **确定性拒绝证据**:引擎路径真实入口上策略拒绝(非审批)产生可查询拒绝记录、不终止对话、与直连呈现等价。
5. **默认启用决策证据**:新增决策记录文档——逐入口证据清单(HTTP 各项 ✓、MCP/workflow 入口如实标注缺口)、放量审计记录核对、默认开关决策(预期:维持全局默认关闭,命名剩余入口证据缺口与翻转前置条件);演进计划 step5d 剩余项行随之更新。
6. **边界声明**:本 change 不修改路由解析、开关机制或入口装配(复用现有 `chat_tool_loop_engine_enabled` + workspace 覆盖 + 会话亲缘);不新增入口。证据测试若暴露行为缺陷,按最小修复处理并在 change 复核记录中登记,不扩大范围。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `unified-chat-execution`:"有工具 agent 经统一运行时执行"补充关态真实入口钉住场景(旧行为保留、旧循环限制显式);"引擎路径具备真实入口证据"补充取消、跨请求多轮连续性、确定性拒绝与默认启用决策记录场景,使"逐入口证据 → 默认开关决策"形成可查询闭环。

## Impact

- `tests/test_channel/test_chat_engine_g3_entry.py`(或新增同层测试文件):新增取消、多轮连续性、关态钉住、确定性拒绝用例;现有关态/开态用例不动。
- `docs/refactor/step5d-g3-default-enable-review.md`(新增决策记录);`docs/refactor/enterprise-agent-platform-evolution-plan.md` step5d 剩余项行更新;与 `docs/design/execution-mechanism-matrix.md`、归档 change 的 S12 交叉引用。
- `openspec/specs/unified-chat-execution/spec.md`:归档时经两处 MODIFIED 需求合入。
- 无生产代码计划改动;若证据暴露缺陷,最小修复限于聊天入口/路由层。
