## Context

Phase R-MVP（PR #117，commit aff59b3）把 `engine/` → `runtime/`、伴生三件归位
（logfold → `runtime/replay/`、temporal → `runtime/temporal/`、harness →
`runtime/self_improvement/`）。R-MVP 的范围是「运行时域 + 机制伴生」；
本 change 是 Phase R-完整 的第一笔：错放件归位（建 `tools/` 域）+ 守护升级
（多前缀阻断、域维度 AST guard）。

现状（c86fb95 = R-MVP merge 基线）：

- `src/hecate/services/observability/` 残留 3 文件：__init__.py + policy_pipeline.py +
  policy_layers.py。后两者 docstring 与实现明确属于 tools 域。
- `tests/test_runtime/test_runtime_self_sufficiency.py` 阻断前缀硬编码为 `hecate.services.*`。
- `tests/test_layering_{llm,sandbox,channel}.py` 守「他方 wheel 不能被特定地方引」（包维度）；
  缺「同包内域目录之间不互引」（域维度）。
- Phase R-完整后续 PR（F2 enterprise/channel/studio/ops 搬运、F3 services/ 清空、F5
  composition 装配层）的 guard 设计需要本 PR 的机制改造。

## Goals / Non-Goals

**Goals:**

- F1：把 `services/observability/` 下两个错放件归位到 `tools/policy/`，建立 `tools/`
  域目录与 `__init__.py`；`services/observability/__init__.py` 改写为过渡位（docstring
  移除 policy_pipeline/policy_layers 提及）。
- F4-A：把 `test_runtime_self_sufficiency.py` 的硬编码阻断前缀改为模块级
  `_BLOCKED_PREFIXES` 列表，新增 `hecate.tools` 一项。**机制从「单一前缀」扩为
  「多前缀」是为 Phase R-完整 后续每个域 PR 复用**——F2 第一个域 PR 不必再做机制改造。
- F4-B：新增 `tests/test_layering_domain.py`，覆盖本 PR 已成型域的子规则
  （`tools/` 零引用其他域、`runtime/` 零引用 `services` 与 `tools`）。

**Non-Goals:**

- 不做 services/tool/ 的其余搬运（registry/builtin/cache/search/shell_*/skill/skill_registry
  等填入 `tools/` 的工作）——属 Phase R-完整 后续 PR
- 不做 enterprise/channel/studio/ops 4 域的搬运——属 Phase R-完整 后续 PR
- 不做 services/ 物理清空
- 不做 services/observability/ 的最终删除（policy_pipeline/policy_layers 移走后该目录只剩
  __init__.py + __pycache__，但 R-MVP commit body 已说「Phase R-完整 处理这些 misplaced
  security-domain files」，本 PR 不动它，让 R-完整 后续 PR 决定是删是留为 placeholder）
- 不做 AST guard 全集——本 PR 仅覆盖已成型域（tools/ + runtime/），其他域按 F2 节奏逐 PR
  增补（先单步守卫再走 PR 是 plan 没明说但符合既有 layering guard 演进模式的做法）
- 不动 composition/ 装配层
- 不开新 change 跟进——本 change 单一 PR 收尾

## Decisions

### D1：错放件归位到 `src/hecate/tools/policy/`（而非 `services/tool/policy/`）

**Decision:** 源文件直接进 `src/hecate/tools/policy/`，并新建 `src/hecate/tools/__init__.py` 作为域目录 marker。

**Rationale:**
- 文件 docstring 与实现语义都明显属于 tools 域（"Composable tool policy pipeline"、
  `SecurityLayer` 包 `ToolAccessPolicy`、所有 layer 操作 `ToolInfo`）
- 「暂留 services/tool/policy/ 等 F2 一并」等于保留目录错位，违反 Phase R 的域优先原则
- F1 建立 `tools/` 目录是 tools 域的奠基石；F2 后续 PR 顺势把 services/tool/{registry,builtin,cache,search,shell_*}
  + services/skill/{loader,parser} + skill_registry/{registry,types} 填充进来

**Alternatives considered:**
- `services/tool/policy/`：保留 services 中层，违反 Phase R 的方向；F2 必须再搬一次（多一次重构 PR）
- `tools/policy_engine/`（更细分子目录）：粒度过细；policy_pipeline 与 policy_layers 是同一抽象的两个面，分目录反而割裂

### D2：runtime self-sufficiency 守护的阻断前缀改为模块级列表

**Decision:** `tests/test_runtime/test_runtime_self_sufficiency.py` 中把硬编码
`hecate.services.*` 字符串替换为模块级 `_BLOCKED_PREFIXES: tuple[str, ...] = (...)`，
并加 `hecate.tools` 一项。探测脚本与子进程元路径阻断器统一读该列表。

**Rationale:**
- F2 每个域 PR 都要扩守护——若机制仍硬编码单前缀，则 F2 第一个域 PR 需做「机制改造 + 首次扩列」两次 PR 工作
- 多前缀阻断对子进程隔离机制无新增复杂度（meta_path finder 早就是 prefix 循环）
- `skip_specs: true` change 不能用 spec 行为约束守住机制改动——本 PR 在 change 描述里明示这是机制改造

**Alternatives considered:**
- 「F1 不动机制，F2 第一个域 PR 再改」：增加 1 个 PR 的碎片化，与现有 layering guard 演进模式（一锤一域）冲突
- 「改为更通用 `hecate.{any domain}.*` 正则」：会误伤 hecate.tools 这种合法引用——前缀列表白名单是更安全的方案

### D3：domain layering guard 只覆盖已成型域（runtime + tools），不写完整 6 域规则集

**Decision:** `tests/test_layering_domain.py` 本 PR 仅写两条已成型域的子规则
（`tools/` 零引其他域、`runtime/` 零引 services+tools）。

**Rationale:**
- 「premature guard」风险：其他域（enterprise/channel/studio/ops）尚未成型，写其子规则意味着
  规则目标位置不存在的对称面会被规则允许但实际是漏洞（守护应反映现状而非愿望）
- 「分层迭代 < 一锤子」：F2 每次新域落地即可追加其子规则，渐进式完整化比一次写齐更鲁棒

**Alternatives considered:**
- 「一次性写 6 域全套规则」：本 PR 阶段其他域目录根本不存在，规则形同虚设
- 「与 F2 各域 PR 同步、每次新域 PR 都带一段」：F2 第一个域 PR 即可补全；本 PR 不必预先埋设占位规则

## Risks / Trade-offs

**[Risk]** F1 错放件归位前 grep 漏检的 import 引用（某路径经字符串拼接、动态加载）导致
测试 / 运行时 ImportError。

→ **Mitigation:** F1 实施后跑定向测试 `tests/test_runtime/test_policy_pipeline.py` + 全量 pytest。
**已完成 explore 阶段 grep**：`grep -rln "policy_layers\|policy_pipeline" src/ tests/ packages/`
只剩 4 个文件（policy_pipeline.py、policy_layers.py、test_policy_pipeline.py、以及自身）。
**该 grep 也会在 commit 前再跑一遍**。

**[Risk]** F4-A 多前缀阻断与 F4-B 域维度 AST guard 重叠（runtime 零引 services 两条都守）。

→ **Mitigation:** 这是设计预期——「子进程阻断」与「静态 AST」是两层独立机制
（前者挡运行时跨包实例化，后者挡源代码 import 语句），重叠是冗余防御不是浪费。
guard 文件 docstring 写明两层的关系。

**[Risk]** Phase R-完整后续 PR（F2 enterprise 等）落地后，F4-B 守护的子规则需要再扩，
可能引入改守护测试本身的 PR。

→ **Mitigation:** 这是计划内的迭代成本。F2 每个域 PR 增 ~10 行规则——PR 数 = 域数（4-5 个），
每个增量小、独立可 revert。

## Migration Plan

本 change 是单一 PR，无部署/回滚复杂度。提交 → push → PR 流程走标准 4 件套验证
（ruff/format/mypy/pytest）。

回滚策略：revert PR 即可恢复 F1 错放件归位前状态；F4-A 改造的 self-sufficiency 守护
机制回退（单前缀阻断）也是单纯 git revert。

## Open Questions

无。F2 各域 PR 的子规则扩展是计划内的迭代而非待定 unknown。