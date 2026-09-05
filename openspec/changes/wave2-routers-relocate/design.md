## Context

Wave 1（PR #121）把 35 个管理路由按域归位后，`src/hecate/api/management/`
剩 5 个 router + `runtime/api/sessions.py` 的归属需要修正。Wave 1 design
D4 把这 5 个定性为"需 composition pattern design 的三方难题"；本 change
explore（2026-09-04）用 AST 级 import 分析重新定性，并经用户拍板修正
sessions 簇归属。现状事实（全部经 `ast.parse` 验证）：

| router | 模块级跨域 import | 自我声明/备注 |
|---|---|---|
| budget | 无（hecate_enterprise 函数内 lazy） | docstring："this module is the enterprise-domain HTTP surface"；无条件挂载 + 请求时 503 |
| traces | 无（只碰 core + models 共享层） | 纯观测查询 |
| collaboration_patterns | runtime.types、studio.workflows.patterns | — |
| conversations | ops.ops_center.conversation_messages、runtime.eventstore | — |
| replay | runtime.eventstore、runtime.replay.logfold、studio.replay.assembler、studio.replay.state_inspector | URL 嵌套于 /sessions/{id}/replay |
| sessions（Wave 1 误放 runtime/api/） | runtime.eventstore、runtime.session_state | 零测试文件引用其模块路径 |

关键机制事实：AST guard（`test_layering_domain.py`）只禁**模块级**跨域
import；函数级 lazy import 是 PR2.1 定稿的合规跨域访问模式（先例：
runtime/tool_access → tools.tool.shell_analysis、coordinator_worker →
studio.workflows.templates）。

## Goals / Non-Goals

**Goals:**
- 5 个遗留 router + sessions 回搬全部归位；`src/hecate/api/` 目录删除
- 全部跨域 import 转函数级 lazy，guard 继续全绿
- enterprise 系 router 挂载模式统一为 guarded mount（auth 先例）
- conversation_messages helper 随消费者归位 studio
- URL / method / schema 100% 不变

**Non-Goals:**
- 不动 hooks.py（runtime/api/ 独守——agent 生命周期拦截属 runtime 面，
  无跨域 import，不是遗留项）
- 不建新 capability；不动 URL / 权限模型
- 不重写 router 内部业务逻辑

## Decisions

### D1. sessions 簇跟产品概念走，归 studio（用户拍板 2026-09-04）

sessions / conversations / replay 是一棵资源树（`/sessions/{id}`、
`/sessions/{id}/replay`、`/conversations`），产品叙事上是 studio 的
"会话"概念。eventstore / session_state 只是存储细节。

- **备选 A（拒绝）**：跟数据源走归 runtime——Wave 1 已把 sessions 放
  runtime/api/，沿用最省事；但 studio/api/ 里没有"会话"概念，产品实体
  与代码组织错位，每次找会话代码都要跨域猜。
- **修正成本**：sessions.py 一次 `git mv` + 2 行 lazy 化；零测试文件
  引用其模块路径，回搬免费。

### D2. composition root 是"选型"机制，不是"访问"机制——修正 Wave 1 D4

Wave 1 D4 预期这 5 个 router 需要"composition pattern design"。explore
推翻：组合根的既有三例（memory_provider / llm_gateway /
channel_providers）全是**选型/替换**语义（多后端挑一个、多 vendor 挂
entry_points）；而 router 借另一个域的 service 是**访问**语义，本
codebase 的合规范式是函数级 lazy import（PR2.1 定稿 + runtime 两个
先例）。**本 change 不引入任何 composition 接线**；"lazy 豁免"通过把
import 挪进函数而成为 guard 合规形态，不再是豁免。

- **备选（拒绝）**：为 5 个 router 建 composition Protocol + resolver
  ——过度设计：它们没有第三方替换面，选型语义不成立。

### D3. budget 挂载改 guarded mount，enterprise 系模式统一

budget 现状是 main.py 无条件 import + 路由内 503；auth 是
try/except ImportError 的 guarded mount（core-only 安装直接跳过挂载）。
统一为 auth 模式：core-only 下不挂载比挂载后报 503 更干净，且
enterprise 系三个 router（auth / api_keys 已 guarded、budget 本次改）模式一致。
budget.py 内部的 lazy import + 503 分支**保留**（防御 wheel 存在但
service 缺失的边缘态），仅挂载层对齐。

### D4. conversation_messages helper 随消费者归位 studio

`ops/ops_center/conversation_messages.py` 的唯一路由消费者是 studio 的
conversations router（消息投影逻辑跟会话走）。随迁到 studio（conversations
router 同目录侧），消除 studio→ops lazy import。若 explore 实现时发现其
他消费者（ops_center 自己的分析面），则改为"留 ops + conversations lazy
import"——实现时以 grep 验证为准，两种走向都不改 spec（skip_specs）。

### D5. collaboration_patterns 的 runtime.types import lazy 化（不豁免）

`runtime.types`（GraphConfig 等）虽偏契约性质，但 models/ 才是既定的
共享契约层；为 runtime.types 开"类型共享"豁免会稀释 guard 的严格性。
统一 lazy 化（1 行），与 D2 的访问范式一致。

## Risks / Trade-offs

- **[sessions.py 回搬产生 studio→runtime lazy import ×2]** → Mitigation：
  lazy import 是 PR2.1 定稿的合规形态且先例存在；guard 只扫模块级，
  转换后 416 守卫用例全绿为准。
- **[lazy 化改变 import 时机]** → 理论风险：函数首次调用才 import，若
  目标模块 import 时有副作用则行为漂移。Mitigation：这 5 个目标模块
  （eventstore / session_state / logfold / patterns / types）均无副作用
  型模块级代码；全量 pytest 兜底。
- **[conversation_messages 迁移方向取决于 grep]** → Mitigation：D4 已写
  明两种走向；实现时验证，不影响 tasks 结构。
- **[budget 挂载模式切换对 core-only 部署的影响]** → 现状 core-only 下
  budget 挂载成功但请求 503；改后直接不挂载。对 API 消费者是 404 vs
  503 的差别——均表示"功能不可用"，OpenAPI schema 中该组路由消失。
  Mitigation：PR description 明示此行为变化（面向 core-only 部署者）。

## Migration Plan

单分支单 PR（squash merge）：6 `git mv` + lazy 化 + main.py + 测试路径
+ 文档收尾，一次合入。回滚即 `git revert`。

## Open Questions

- conversation_messages 的迁移方向（D4）：实现时 grep 全仓库消费者后定，
  两种走向均已预授权，无需再问。
