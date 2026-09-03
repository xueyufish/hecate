## Why

Phase R-MVP（#117）完成了 `engine/` → `runtime/` 重命名与 logfold/temporal/self_improvement
三个伴生目录的归位，但 `src/hecate/services/observability/` 下仍残留两个「错放件」
（policy_pipeline.py 与 policy_layers.py）——它们的 docstring 与实现都明确属于 **tools 域**
（composable tool policy pipeline、ToolAccessPolicy 包装层），却挂在 observability 目录下。
本次 change 把这两个文件归位到 `src/hecate/tools/policy/`，建立 tools 域目录的奠基石
（Phase R-完整 后续 PR 将继续把 services/tool/、services/skill/、skill_registry/ 填充进来）。

顺带完成 Layering Guard 的域维度升级：F1 建立的 `tools/` 域从诞生第一刻起就被守护，
且 `tests/test_runtime/test_runtime_self_sufficiency.py` 的子进程阻断机制从「单一
`hecate.services.*` 前缀」扩为「多前缀模块级列表」，为 Phase R-完整后续每个域 PR
复用同一机制。

无 spec 行为变化（错放件归位与守护机制升级均为纯重构），声明 `skip_specs: true`。

## What Changes

- `src/hecate/services/observability/policy_pipeline.py` → `src/hecate/tools/policy/policy_pipeline.py`
- `src/hecate/services/observability/policy_layers.py` → `src/hecate/tools/policy/policy_layers.py`
- `src/hecate/services/observability/__init__.py` → `src/hecate/tools/policy/__init__.py`（docstring 改为
  「Tool policy pipeline — composable evaluation layers in fixed order:
  PluginAvailability → Profile → Visibility → Security → Mode」）
- `src/hecate/services/observability/__init__.py` 改为纯 placeholder 文件，docstring 移除 policy_layers/policy_pipeline
  引用（保留为待 R-完整后续 PR 清空或删除的过渡位）
- `tests/test_runtime/test_policy_pipeline.py`：4 处 import 路径从
  `hecate.services.observability.policy_{pipeline,layers}` → `hecate.tools.policy.*`
- 新增 `src/hecate/tools/__init__.py`（空 package marker）

守护与机制升级：

- `tests/test_runtime/test_runtime_self_sufficiency.py`：将硬编码的「阻断 `hecate.services.*`」
  改为模块级 `_BLOCKED_PREFIXES` 列表，新增 `hecate.tools` 一项；探测模块清单不变。
  为 Phase R-完整 后续域 PR（enterprise/channel/studio/ops）按「每建一域即扩一前缀」模式铺路。
- 新增 `tests/test_layering_domain.py`：域维度 AST guard（mirror `test_layering_sandbox.py` 模式）。
  本 PR 覆盖两条已成型域的子规则：
  - `tools/` 零模块级 import 任何其他 `hecate.<域>.*`（enterprise/channel/studio/ops/runtime）
  - `runtime/` 零模块级 import `hecate.services.*` 与 `hecate.tools.*`（services 已由原守护覆盖；
    tools 是本 PR 新增前缀）

## Capabilities

### New Capabilities

无。纯重构，无 spec 行为变化。

### Modified Capabilities

无。纯重构，无 spec 行为变化。

（声明 `skip_specs: true`——本 change 不引入也不修改任何 spec 行为。）

## Impact

- **Affected code**: `src/hecate/services/observability/`（清空为仅留 __init__.py）、
  `src/hecate/tools/`（新建目录）、`tests/test_runtime/test_policy_pipeline.py`、
  `tests/test_runtime/test_runtime_self_sufficiency.py`、新增 `tests/test_layering_domain.py`
- **APIs**: 无 public API 变化（错放件只是换目录位置；`hecate.services.observability` namespace
  不再导出 ToolPolicyPipeline 相关符号——但既无第三方消费者，也无内部 `from hecate.services.observability import`
  该符号的引用，确认后下游唯一引用是 `tests/test_runtime/test_policy_pipeline.py` 的 4 处 import）
- **Dependencies**: 无
- **Systems**: 无