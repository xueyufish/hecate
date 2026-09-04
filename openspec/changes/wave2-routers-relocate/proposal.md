## Why

api-management-relocate（PR #121，Wave 1）把 35 个管理路由按域归位后，
`src/hecate/api/management/` 还剩 5 个跨域 router（budget /
collaboration_patterns / conversations / replay / traces）。Wave 1 design
D4 当时将其定性为"需 composition pattern design"的三方难题；本 change 的
explore（2026-09-04 AST 级 import 分析）推翻了该定性：5 个 router 中 2 个
零模块级业务 import、3 个只需把 1–2 行 import 挪进函数（函数级 lazy import
是 PR2.1 定稿的合规跨域访问模式，runtime/AGENTS.md 已有先例）——不存在
需要组合根的"选型"问题。此外用户拍板（2026-09-04 explore）：sessions 簇
（sessions / conversations / replay）跟产品概念走，归 studio/——Wave 1
把 sessions 放 runtime/api/ 的决定随之修正。

本 change 兑现 plan §1.1 的最后一个字面承诺：**全局 `api/` 分层消失**。

## What Changes

- **6 个 `git mv`**：
  - `api/management/budget.py` → `enterprise/api/budget.py`（docstring
    自我声明 enterprise 表面；挂载从"无条件 + 503"改为 auth 同款
    guarded mount，enterprise 系模式统一）
  - `api/management/traces.py` → `ops/api/traces.py`（纯观测查询，零改动）
  - `api/management/collaboration_patterns.py` → `studio/api/`（1 行
    runtime.types import lazy 化）
  - `api/management/conversations.py` → `studio/api/`（2 行 lazy 化：
    ops.ops_center.conversation_messages + runtime.eventstore）
  - `api/management/replay.py` → `studio/api/`（2 行 lazy 化：
    runtime.eventstore + runtime.replay.logfold；studio.replay.* 2 处
    留模块级，同域合法）
  - `runtime/api/sessions.py` → `studio/api/sessions.py`（**Wave 1 决定
    修正**：2 行 lazy 化 runtime.eventstore + runtime.session_state；
    零测试文件直接引用其模块路径，回搬免费）
- **helper 随迁**：`ops/ops_center/conversation_messages.py` →
  `studio/conversations 侧`（消息投影 helper 的消费者是 studio 的
  conversations router——错放件归位，避免 studio→ops lazy import 成为
  永久豁免）
- **main.py**：6 行 import 重写 + budget 挂载改 guarded mount
- **收尾**：`src/hecate/api/` 目录删除；URL / method / schema 全部不变

## Capabilities

### New Capabilities
<!-- none: 纯文件位置重构 + lazy 化，无 spec 级行为变化 -->

### Modified Capabilities
<!-- none -->

**`skip_specs: true`** —— 路由 URL、CRUD 语义、权限模型均不变；lazy 化
只改 import 时机，不改行为。

## Impact

- **6 个 router 文件**位置变化（其中 sessions 是 Wave 1 决定回搬）
- **1 个 helper**（conversation_messages）随消费者归位 studio
- **main.py**：6 行 import + budget 挂载模式
- **测试**：sessions/conversations/replay 等的 `patch()` 字符串路径少量
  更新（sessions 模块路径零测试引用；全量 grep 后按实际命中改）
- **AST guard**：全部跨域 import 转为函数级后 guard 继续全绿；guard
  测试本身零改动
- **docs/research/industry-architecture-comparison.md**：§1.4 主包行
  "遗留 5 个跨域 router"注记删除；§1.1 分歧注 ⑧ 删除（承诺全部兑现）；
  §3.7 工期表补本 PR
- **依赖**：无外部依赖变更

## Non-goals

- 不动 hooks.py（留 runtime/api/——agent 生命周期拦截属 runtime 面，
  无跨域 import，非遗留项）
- 不动 URL / schema / 权限模型
- 不建新 capability（纯重构）