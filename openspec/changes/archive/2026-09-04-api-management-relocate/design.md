## Context

Phase R (PR #117–#120, commit `ec5cfdf`) 在主包完成六域 modular monolith
重排：六个域目录 `runtime/tools/enterprise/channel/studio/ops` +
`core/composition` 装配层 + `models/` 纵贯层。剩余债：~6,937 行 FastAPI
管理面路由仍挂在全局 `src/hecate/api/` 顶层，违反 plan §1.1 "全局 api/
分层消失——原则。PR #118 当时只把 `api/v1/` 搬到了 `channel/api/v1/`；
`api/management/*` 41 个 router + `api/{audit,auth,evaluation,middleware,
schedules,security_findings,tool_decisions}.py` 6 个顶层文件被压到后续。

现状汇总（见 `proposal.md` §Impact 详表）：
- 37 文件 in `src/hecate/api/management/`（共 5,946 行）
- 6 文件 in `src/hecate/api/` 顶层
- 1 `api/middleware.py`（`AuditMiddleware`，已存在 `core/middleware.py` 占位）
- 真实归属：studio 7 / tools 5 / ops 17（含 system/backup） / enterprise 3 /
  channel 1 / core 2 / runtime 2、3 个棘手 router 留第二波

约束：
- URL 100% 不变（HTTP 路径、method、request/response schema）
- AST guard（`test_layering_domain.py`）不许引入新的跨域违例
- main.py 是当前路由的唯一挂载点
- 不解决 3 个棘手 router 的 composition design（独立 change）

## Goals / Non-Goals

**Goals:**
- 把 40 router + 1 middleware 按真实归属搬入对应域的 `api/` 子目录
- `main.py` 的 40 行 import 路径改为 `from hecate.<domain>.api.<x> import router`
- 约 10 个测试文件的 `patch.object` / 直接 import 路径同步
- `docs/research/industry-architecture-comparison.md` §1.4 主包行删除"遗留
  41 个管理路由待归域"注记；§1.1 树分歧注 ⑧ 删除
- 全部验证：ruff / format / mypy / 全量 pytest / pre-commit hook（rule 不变）

**Non-Goals:**
- 不解决 3 个棘手 router（`model_providers` / `budget` / `sessions/replay/
  conversations`）的 composition design
- 不新建 OpenSpec capability（pure refactor；`skip_specs: true`）
- 不动 HTTP URL、method、CRUD 语义、权限模型、事件流
- 不重构 router 内部实现（仅位置搬迁）
- 不动 `channel/api/v1/`、`channel/management/alerts.py`（已归位）

## Decisions

### D1. 路由器位置策略：每个域 `api/` 子目录

**选择**：在每个目标域目录下建 `api/` 子目录承载管理面路由。
例如 `src/hecate/ops/api/audit.py` 而非 `src/hecate/ops/audit_api.py`。

**备选**：
- A. 命名 `api.py` 单文件（聚合该域所有路由）—— 拒绝：单文件会很快破千行，
  失去 plan §1.1 的"按业务模块切"原则
- B. 域根目录直接放（`src/hecate/ops/audit.py`）—— 拒绝：与域内核文件
  （`scheduler/`、`backup/` 等子目录）混层，违反现有"内核 +伴生 + api"
  三层节奏
- C. 复用现有 `src/hecate/api/` 改名为 `src/hecate/_api/` 或类似—— 拒绝：
  拖延问题没解决

**理由**：`src/hecate/<domain>/api/` 是 plan §1.1 既定节奏（PR #118 已
为 `channel/` 建过 `channel/api/v1/`、`channel/management/`），与现行
约定一致。

### D2. main.py 改动粒度：单 commit 单 PR

**选择**：所有 main.py import 路径修改 + 全部 router `git mv` 在一个 PR
合入，按域一次完成（不用按域拆 PR 链）。

**备选**：
- A. 按域拆 5–7 个 PR——拒绝：每个 PR 都得让 main.py 处于"半新半旧"
  状态；CI 风险叠加
- B. 按棘手程度分两 PR（先明确归属 37 个，再 3 个棘手）——拒绝：第一波 PR
  与第二波 PR 的 import 路径不一致，main.py 必须二次改，复杂度高于一次

**理由**：纯机械搬运、URL 不变、AST guard 不变——单 PR 风险可控；
review 时一次性看完 40 router 的归属映射决策，比 7 次更省 reviewer。

### D3. middleware 放置：`core/middleware/audit.py`

**选择**：把 `AuditMiddleware` 从 `api/middleware.py` 搬到
`src/hecate/core/middleware/audit.py`（`core/middleware.py` 占位文件已存在，
`mypy_path` 与 `core/composition` 同级）。

**理由**：`AuditMiddleware` 数据汇点是 `ops/audit/store.py`，本身无业务域
归属；放 `core/` 是纵贯层基础设施最自然的位置；`core/middleware.py`
占位文件已存在表明规划原意即此。

### D4. 3 个棘手 router 的搬运策略：本 change 跳过

**选择**：`model_providers.py` / `budget.py` / `sessions.py` / `replay.py` /
`conversations.py`（5 文件、~1,178 行）**不**进本 change 的 tasks。它们
的归属与 composition 决策属于独立 OpenSpec change，本 change 不强行
搬运以免 design 不成熟导致回退。

**理由**：plan §1.1 的"按域自包含"原则要求棘手 router 的跨域依赖
走 composition root；composition 的边界（哪些 service 暴露、何时实例
化、如何 mock）本身就是 design 决策——与本 PR 的"机械搬运"性质不混。

**风险**：3 个棘手 router 仍留在 `src/hecate/api/` 顶层，main.py 也保留
对它们的 import。但分层方案 C 的全部叙事都基于"第二波解决棘手"——
本 change 完成后 main.py 仍有 5 行 `from hecate.api.{auth,budget,model_
providers,...}` 的 import 残留。

### D5. AST guard 与 router 路径：现状可兼容

**选择**：不修改 `tests/test_layering_domain.py`。

**理由**：guard 检查的是"已建立域之间不互 import"。router 进入域内
`api/` 子目录仍属该域；3 个棘手 router 在 `api/` 顶层（不进本 change）
属于 guard 之前的特例 `OTHER_DOMAINS = ('services', 'enterprise', ...)`，
且 `api/` 不在列表——`api/` 顶层代码与所有域的互向 import 也未在 guard
内被检查。本 change 不引入新违例，guard 测试无需改。

### D6. 测试改动：sed 批量 + 个别手改

**选择**：用 `grep -lr "hecate\.api\.management"` 找出所有测试文件；用
`from hecate.api.management.X import` 替换为 `from hecate.<domain>.api.X
import`；约 10 文件受影响。

**理由**：上轮 PR #120 已经验证过这模式（executor 测试的重写）。纯 sed
可覆盖 95% 路径；剩余 5% 是 `from hecate.api.management import X as
mod` 形式的 lazy module patch（test_layering_domain 与 test_api/test_*
少量），逐文件 review 处理。

## Risks / Trade-offs

- **[3 个棘手 router 留在 `api/` 顶层]** → Mitigation：本 PR description
  明示"待第二波 OpenSpec 解决"；`docs/research/...` §1.4 的"遗留"注记
  改为"已部分归域（30+1 middleware 到位，5 个跨域棘手 router 待
   composition design）"。
- **[main.py 改动 40 处]** → Mitigation：单 commit 单 PR，main.py 的 diff
  按域分组（`# --- ops ---`、`# --- studio ---`）便于 reviewer 理解。
- **[router 内部 `from hecate.api.X import` 互相引用]** → 中风险：
  调研显示无 router-to-router 直接 import，但若有则迁移时 break。Mitigation:
  `git mv` 后立刻 `ruff check src/` 兜底，CI 把关。
- **[AuditMiddleware 的 `app.add_middleware()` 调用点]** → 低风险：
  main.py line 189 是唯一调用点；改为
  `from hecate.core.middleware.audit import AuditMiddleware`。
- **[测试 `patch("hecate.api.management.X")` 的字符串路径]** → 改路径；
  自动化 grep 检测所有 patch 字符串。

## Migration Plan

1. 新分支 `opsx/api-management-relocate`（已建）。
2. 按域逐批 `git mv`：
   - core: 2 files（feature_flags, i18n）
   - runtime: 2 files（sessions, hooks）
   - tools: 5 files（mcp, skill_registry, skills, tool_cache, tools,
   tool_policies）
   - channel: 1 file（a2a）
   - ops: 17 files（含 system/backup/ middleware）
   - studio: 7 files
   - enterprise: 3 files（auth, api_keys, model_providers）
3. middleware：`api/middleware.py` → `core/middleware/audit.py`
4. main.py：~40 行 import 改写 + 1 行 add_middleware 改写
5. 测试：~10 文件 sed
6. 验证：ruff + format + mypy + 全量 pytest + smart-pytest.sh ops 域集
7. docs：删 research doc 的"遗留"注记 + §1.1 分歧注 ⑧
8. 单 PR + squash merge

**回滚策略**：单 PR，回滚即 `git revert <sha>`；router 位置搬迁不会
引入新依赖，回滚后行为等价。

## Open Questions

- **`core/middleware/audit.py` vs `core/middleware/audit_middleware.py`**
  命名？倾向 `audit.py`（与 `core/feature_flags/` 内 `service.py` 同款）
  —— 等用户在 PR review 时定。
- **`tools/api/` 与 `tools/mcp/` 的并列关系**：router 进 `tools/api/`，
  mcp 是域内核。倾向保持 `tools/api/` 而非 `tools/mcp/api/`——router 是管理
  面，mcp 是 service 面。等用户在 PR review 时定。

> 注：3 个棘手 router 的 composition design 问题已超出本 change 范围，
> 留作独立 OpenSpec change 的 proposal/设计对象。