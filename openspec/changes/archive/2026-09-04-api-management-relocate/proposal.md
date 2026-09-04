## Why

Phase R (PR #117–#120) 完成主包域优先重组后，`src/hecate/api/` 顶层仍残留
40 个管理面 FastAPI 路由（37 文件在 `api/management/`、3 文件在 `api/`
顶层、另含 1 `middleware.py`），合计 ~6,937 行。它们违反 plan §1.1 的
"按业务域切模块，每模块自包含路由与服务；纵贯层仅保留 core 与 models"，
但主 PR 因工作量与跨域 design 未决被拆分保留。已沉淀调研（router 归属
表 + 3 个棘手跨域 router 的 design 选项）但未走 OpenSpec 立项。

## What Changes

按**分层方案 (C)** 执行：

- **第一波（机械搬运波，~30 个 router）**：把归属明确、无跨域 service
  import 的 router `git mv` 到目标域 `api/` 子目录；`main.py` 的
  `from hecate.api.<x> import router` 改写为 `from hecate.<domain>.api.<x>
  import router`；10 个测试文件里的 `patch.object` / 直接 import 改路径；
  URL 100% 不变。
- **第二波（棘手 router，3 个）**：单独 design PR 解决 `model_providers`
  (enterprise+hecate_llm 跨 wheel)、`budget` (ops+hecate_enterprise)、
  `sessions`/`replay`/`conversations` (runtime+studio+ops 三方)。
  这三个不进本 change 的 tasks.md。
- **第二波附（边界 router，~4 个）**：`feature_flags`/`i18n` → `core/`
  纵贯层；`hooks` → `runtime/hooks/`；`auth`/`api_keys` → `enterprise/auth/`。
- **`api/middleware.py`**：`AuditMiddleware` 拆到 `core/middleware/audit.py`
  （已存在 `core/middleware.py` 占位文件）。
- **不动**：`api/v1/`（已归 `channel/api/v1/`）+ `api/management/alerts.py`
  （已删）+ `channel/management/alerts.py`（live 路由）。

## Capabilities

### New Capabilities
<!-- none: 纯路由文件位置重构，无 spec-级别行为变化 -->

### Modified Capabilities
<!-- none: HTTP URL 不变、路由语义不变 -->

**`skip_specs: true`** —— 路由 URL、CRUD 语义、权限模型、事件流均不变；
本 change 不引入新需求、不修改现有需求，pure refactor。

## Impact

- **40 router 文件位置**：从 `src/hecate/api/management/*` 与
  `src/hecate/api/*.py` 搬到各目标域 `api/` 子目录。
- **`main.py`**：~40 行 `from hecate.api.X import router` 改写为
  `from hecate.<domain>.api.X import router`。
- **测试**：`tests/test_api/` 50 文件大多按 HTTP 测，URL 不变即零改动；
  约 10 个文件用 `patch(...)` 直接引 router 模块路径，需 sed 改路径。
- **AST guard**（`test_layering_domain.py`）：现状已禁跨域 structural import；
  搬运后 router 进入各域 api/ 子目录，归属检验天然正确；guard 测试无需改。
- **3 个棘手 router 的 service 依赖**：搬运后跨域 import 仍 lazy（不动），
  不引入新的 layering 违例，但也不解决"按域自包含"原则——那是第二波
  design 的事。
- **docs/research/industry-architecture-comparison.md §1.4** 主包行更新：
  删"遗留：顶层 api/management/* 41 个管理路由待归域"注记；§1.1 树
  分歧注 ⑧ 删除。
- **AGENTS.md**：无影响（无新规则）。
- **依赖**：无外部依赖变更。

## Non-goals

- 不解决 3 个棘手 router 的跨域 composition design（独立 change）
- 不动 `api/v1/`（已归 channel/api/v1/）
- 不动路由 URL、请求/响应 schema、权限模型
- 不新建 OpenSpec capability（纯重构，无 spec-级别行为变化）