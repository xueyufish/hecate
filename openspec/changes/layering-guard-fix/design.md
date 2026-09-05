## Context

`test_layering_domain.py` 与 `test_runtime_self_sufficiency.py` 是
Phase R 双层防线（AST 源码扫描 + subprocess import 探针）。本次发现
两层对"域内规则"同时失效：

1. **AST 层空转**：`_is_other_domain_import` 匹配 `module == prefix or
   module.startswith(prefix + ".")`，prefix 为裸域名（`ops`）。实际
   import 是 `hecate.ops.alerts.service`，module 字符串以 `hecate.`
   开头，永不命中。sibling-packages 规则（tools/enterprise/channel/
   studio/ops 的反向）直接写 `hecate.tools.` 全前缀，不受影响。
2. **probe 白名单漏项**：`CORE_RUNTIME_MODULES` 是显式 25 模块白名单，
   不含 `runtime.agent_execution_port`、`runtime.security.egress`、
   `runtime.security.hooks.output_security`——三个带模块级域 import
   的文件从未被探针触及。

合成违规已实证：studio/ 内新增模块级 `from hecate.ops.audit import
service`，`TestStudioDomainNeverImportsOtherDomains` 照样通过。

## Goals / Non-Goals

- Goal：guard 修后**真实拦截**；12 处既有违规全部收敛到文档承认的
  豁免机制（函数级 lazy / TYPE_CHECKING）；双层防线重新覆盖同一批文件
- Non-Goal：架构级反转（DI / protocol / entry_points / 搬家）——
  见 proposal Non-goals

## Decisions

### D1 — matcher 归一化（1 行 + 1 回归）

`_is_other_domain_import` 入口处 `module = module.removeprefix("hecate.")`。
不动 OTHER_DOMAINS / 调用方 / sibling 规则。回归测试：
`assert _is_other_domain_import("hecate.ops.x") == "ops"`（外加裸前缀
与非域路径的负例）。理由：修在 matcher 一处，六条规则全部生效；
sibling 规则写法本就正确，不动。

### D2 — runtime → studio：agent_execution_port 函数内 lazy

`runtime/agent_execution_port.py:36-40` 模块级导入 studio.agents.handoff
的 3 个函数，实际调用点在方法体内（139 / 190 / 207）→ import 下沉到
3 个调用点函数内。与 runtime/AGENTS.md 既有 sanctioned lazy import
（coordinator_worker → studio.workflows.templates）同款。文件已有
`from __future__ import annotations`，无注解引用需处理。

> **实施补记（2026-09-05 apply）**：把该文件纳入 probe 白名单后，探针
> 钓出第 4 条隐藏边——同文件模块级 `from hecate_ops.span_adapter import
> create_otel_span, end_otel_span`（轮子边界；create_span / end_span
> 两个方法体内使用）。同款函数内 lazy 收敛。PR3b 曾以"span_adapter 被
> agent_execution_port 模块级 import"为由把 hecate-ops 定为 required
> 依赖——该理由对本文件已不成立（runtime_port_adapter 仍模块级依赖，
> required 定级不变）。

### D3 — runtime → ops：security/egress 双机制

`egress.py:32-33` 导入 `DLPAction` / `DLPFinding` / `DLPScanner`：
- `DLPFinding`：仅 dataclass 字段注解（62 / 85）→ TYPE_CHECKING
- `DLPScanner`：仅构造器签名注解（105；scanner 经 guardrail_assembly
  DI 注入，本文件不实例化）→ TYPE_CHECKING
- `DLPAction`：运行时值使用（action 映射方法）→ 映射方法内函数级 import

文件已有 future annotations，TYPE_CHECKING 注解零运行时开销。

### D4 — runtime → ops：hooks/output_security 函数内 lazy

`output_security.py:10` 导入 `FindingWriterAdapter` /
`SecurityFindingWriter`，唯一运行时使用是 374 行 isinstance 检查
（方法体内）→ 函数级 import 下沉到该方法。

### D5 — channel v1 路由 lazy 化（agents.py + chat.py）

- `agents.py:43` `session_lock_manager`（模块级单例）：唯一使用在
  156 行 endpoint 内 → 函数内 import
- `chat.py:35` 同款（使用在 173）
- `chat.py:36` `WorkflowExecutionService`：使用在 407（`_process_*`
  helper 内）→ 函数内 import
- `chat.py:37` `ToolRegistry`：使用在 `_build_tool_registry`
  （689）→ 函数内 import + 返回注解改字符串 `"ToolRegistry"`
  （future annotations 已就位，直接留注解亦可）

### D6 — channel/management/alerts.py：helper 收敛

`AlertService(db, workspace_id=ctx.workspace_id)` 在 ~10 个 endpoint
内重复构造 → 新增模块级 `_alert_service(db, ctx)` helper（函数内
import，返回注解字符串化），10 处调用点改走 helper；
`NotificationDispatcher()`（279，单点）→ 就地函数内 import。
import 行为不变、构造参数不变。

### D7 — ops → channel：notification.py 按符号分流

`ops/notification.py:13-18` 导入 4 个符号：
- `NotificationChannelAdapter`：仅注解（190 / 192 / docstring）→
  TYPE_CHECKING
- `WebhookNotificationAdapter` / `WebSocketNotificationAdapter` /
  `EmailNotificationAdapter`：仅在 `_build_adapter_map`（190）内
  实例化 → 该函数内函数级 import

### D8 — ops → studio：conversation_messages 函数内 lazy

`conversation_messages.py:18` `derive_session_messages` 唯一使用在
61 行函数体内 → 函数内 import。（Wave 2 D4 已把反向边
studio/api/conversations.py → ops 收敛过；本条收敛同一条缝的另一半。）

### D9 — probe 白名单 +3（D2–D4 落地后）

`CORE_RUNTIME_MODULES` 增补 `hecate.runtime.agent_execution_port`、
`hecate.runtime.security.egress`、`hecate.runtime.security.hooks/
output_security`。顺序约束：必须在 D2–D4 之后，否则探针立刻红。
白名单机制本身保留（显式清单 = 可审计），缺口由 AST 全目录扫描兜底。

### D10 — 文档 ride-along

- `channel/management/__init__.py` docstring：删除"triage 未落地 /
  文件归 api/ 所有"的 pre-Wave-1 措辞，改为如实描述（alerts 路由的
  永久域内住所；跨域访问走函数级 lazy）
- `runtime/AGENTS.md`：合规 lazy import 清单从 2 条扩为全量
  （+agent_execution_port→handoff、egress→dlp、output_security→
  findings_writer），并把"双层防线覆盖"的验证方式写明
- research doc（gitignored，主 checkout 本地改，不进 commit）：
  顶部进度表 Phase R 行 "#117–#120" → "#117–#122"；§3.7 补本 PR

## Risks / Trade-offs

- **lazy 化掩盖真实架构边**：函数级 import 仍是"runtime 知道 studio"
  的语义耦合，只是不构成 import 时的结构耦合。接受——这是 PR2.1
  定稿、runtime/AGENTS.md 记档的既有取舍；架构级反转走独立 change。
- **guard 修严后新 PR 引入违规会被拦**：这正是目的；报错信息含
  文件：行号，修复模式有先例可循。
- **probe 白名单仍可能漏未来新文件**：AST 规则现已是全目录扫描且
  真实生效，双层冗余恢复设计意图。

## Migration Plan

单 PR：guard 修复 + 12 处收敛同 commit（guard 先红后绿的中间态只
存在于本地开发过程）。验证顺序：guard 单元回归 → guard 三件套
（layering_domain / runtime_self_sufficiency / core_self_sufficiency）
→ 全量 pytest + ruff + mypy → `import hecate.main` 冒烟。
