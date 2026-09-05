## Why

Phase R 收尾核验（2026-09-05）发现 `tests/test_layering_domain.py` 的
`_is_other_domain_import` 用**裸前缀**（`ops.`）匹配，而代码中实际
import 均为全限定（`hecate.ops.*`）——六条域内规则（tools / runtime /
enterprise / channel / studio / ops）自写下以来从未拦截过任何东西
（合成违规验证：studio 内模块级 `from hecate.ops.audit import service`
照样通过）。这与 #120 查出的 smart-pytest.sh 静默失效同类：**闸门空转**。

用修正后的匹配器全库扫描，排除 runtime（engine 层合法被所有域消费）后，
真实跨域模块级 import 共 **9 处**；其中更严重的是 **3 处 runtime → 域**
（违反 runtime 自足不变量，卡死未来 hecate-runtime 轮子拆出）。
runtime probe（`test_runtime_self_sufficiency`）能过是因为其
`CORE_RUNTIME_MODULES` 白名单恰好不含这 3 个文件——AST 层空转 +
白名单漏项，两层防线对同一批文件同时失效。

## What Changes

- **guard 修复（1 行）**：`_is_other_domain_import` 对 module 先
  `removeprefix("hecate.")` 再匹配；补 1 个 matcher 单元回归
  （`"hecate.ops.x"` → `"ops"`），钉死本 bug
- **12 处违规收敛**（零文件搬家，全部走既有豁免机制）：
  - 3 处 runtime → 域（agent_execution_port → studio.agents.handoff；
    security/egress → ops.dlp.*；security/hooks/output_security →
    ops.security.findings_writer）→ 函数内 lazy / TYPE_CHECKING
  - 9 处域 → 域（channel v1 路由 → studio/tools；channel/management/
    alerts → ops；ops/notification → channel；ops/ops_center/
    conversation_messages → studio.replay.assembler）→ 同款 lazy 化
- **probe 白名单补 3 个模块**：`CORE_RUNTIME_MODULES` 增补
  agent_execution_port + security/egress + security/hooks/
  output_security（修复后三者 import-clean，白名单缺口闭合）
- **文档 ride-along**：channel/management/__init__.py 过时 docstring
  刷新（"triage 未落地"措辞已失效）；runtime/AGENTS.md 合规 lazy
  import 清单从 2 条扩到实际清单；research doc 顶部进度表 Phase R 行
  "#117–#120" → "#117–#122"（gitignored，仅本地）

## Capabilities

### New Capabilities
<!-- none: guard 修复 + import 时机调整，无 spec 级行为变化 -->

### Modified Capabilities
<!-- none -->

**`skip_specs: true`** —— 不改 URL / 语义 / 权限；lazy 化只改 import
时机；guard 修复是测试层强化（当前为空转，修后行为=文档声称的行为）。

## Impact

- **9 个源文件** import 时机调整（无签名 / URL / schema 变化；
  `from __future__ import annotations` 已在全部 6 个核心文件就位，
  注解类符号进 TYPE_CHECKING 零运行时影响）
- **tests/test_layering_domain.py**：matcher 修复 + 1 个单元回归
- **tests/test_runtime/test_runtime_self_sufficiency.py**：
  CORE_RUNTIME_MODULES +3
- **docs**：channel/management/__init__.py docstring、
  runtime/AGENTS.md 合规清单、research doc（本地）
- **依赖**：无外部依赖变更

## Non-goals

- 不搬家：alerts service 留 ops/、handoff helpers 留 studio/、
  NotificationDispatcher 不做 protocol/entry_points 反转——本次只修
  闸门 + 把现有边收敛到合规豁免机制；架构级反转留独立 change 按需评估
- 不做 guard 白名单/豁免清单机制——违规要么修掉，要么走函数级 lazy
  / TYPE_CHECKING 两个既有豁免，不让模块级违规"注册即合法"
- 不动 sibling-packages 规则（`hecate.tools.` 全前缀写法本来就是对的）
