## 1. F1 错放件归位

- [ ] 1.1 在 main checkout 创建空目录 `src/hecate/tools/` 与子目录 `src/hecate/tools/policy/`，写 `src/hecate/tools/__init__.py`（空 package marker）
- [ ] 1.2 `git mv src/hecate/services/observability/policy_pipeline.py src/hecate/tools/policy/policy_pipeline.py`
- [ ] 1.3 `git mv src/hecate/services/observability/policy_layers.py src/hecate/tools/policy/policy_layers.py`
- [ ] 1.4 把 `src/hecate/services/observability/__init__.py` 的 docstring 重写：移除「policy_pipeline / policy_layers」相关提及（policy_pipeline 与 policy_layers 已迁出该目录）；保持 placeholder 形态待 R-完整后续 PR 处置
- [ ] 1.5 新建 `src/hecate/tools/policy/__init__.py`：docstring 描述「Tool policy pipeline — composable evaluation layers in fixed order: PluginAvailability → Profile → Visibility → Security → Mode. Lives in the tools domain (was misplaced under services/observability before PR phase-r-domain-reorg-followups).」
- [ ] 1.6 `sed` 重写 `tests/test_runtime/test_policy_pipeline.py` 的 4 处 import：`hecate.services.observability.policy_pipeline` → `hecate.tools.policy.pipeline`、`hecate.services.observability.policy_layers` → `hecate.tools.policy.layers`
- [ ] 1.7 全仓再 grep 一次 `policy_layers\|policy_pipeline` 确认仅剩 3 个文件（policy_pipeline.py、policy_layers.py、test_policy_pipeline.py）

## 2. F4-A 自足守护多前缀化

- [ ] 2.1 在 `tests/test_runtime/test_runtime_self_sufficiency.py` 顶部新增模块级常量 `_BLOCKED_PREFIXES: tuple[str, ...] = ("hecate.services", "hecate.tools")`
- [ ] 2.2 把 `_OptionalPackageBlocker.find_spec` 里的硬编码 if 替换为对 `_BLOCKED_PREFIXES` 的循环判定
- [ ] 2.3 `_run_subprocess_probe` 调用点的 `_BLOCKED` 替换为对 `_BLOCKED_PREFIXES` 的引用
- [ ] 2.4 顶部 docstring 补一段：「从硬编码 `hecate.services.*` 单前缀扩为模块级列表——Phase R-完整 后续每个新域在列表中追加一行」

## 3. F4-B 域维度 AST guard

- [ ] 3.1 新建 `tests/test_layering_domain.py`：3 类测试
  - `TestToolsDomainNeverImportsOtherDomains`：`tools/` 零模块级 import `hecate.{enterprise,channel,studio,ops,runtime}.*`（按子模块判定，TYPE_CHECKING / 函数体内 / try-ImportError 豁免）
  - `TestRuntimeDomainNeverImportsBlockedPrefixes`：`runtime/` 零模块级 import `hecate.services.*` 与 `hecate.tools.*`（AST 模块级扫描；与 self-sufficiency 守护是两层独立机制——前者挡 import 语句，后者挡运行时实例化）
  - `TestOtherPackagesImportToolsSelfOnly`：将来 F2 把 `services/tool/` 等内容填入 `tools/` 后，sibling packages 与 core 非 tools 域不得 import `hecate.tools.*`——本 PR 此刻只为形态预留测试骨架（`pytest.skip` 当 tools/ 域外尚无依赖）
- [ ] 3.2 守护 docstring 写明两层机制的关系（AST 静态扫描 vs self-sufficiency 子进程阻断）与 F2 后续 PR 增补节奏

## 4. 验证

- [ ] 4.1 `ruff check src/hecate/ packages/ tests/` 全绿
- [ ] 4.2 `ruff format --check src/hecate/ packages/ tests/` 全绿
- [ ] 4.3 `uv run mypy src/ packages/` 全绿
- [ ] 4.4 `python -m pytest tests/test_runtime/test_policy_pipeline.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_layering_domain.py tests/test_layering_sandbox.py tests/test_layering_llm.py tests/test_layering_channel.py -q` 全绿
- [ ] 4.5 全量 `python -m pytest tests/ -q` 全绿
- [ ] 4.6 `git grep -n "hecate\.services\.observability\.policy_" src/ tests/ packages/` 零命中

## 5. 提交

- [ ] 5.1 `git add -A` 后写 conventional commit：`refactor(domain): move tool policy pipeline to tools/policy + extend layering guards (Phase R follow-ups)`
- [ ] 5.2 commit body 包含：F1 移动文件清单 + F4-A 机制改造说明 + F4-B 当前覆盖域 + 验收四件套数据 + 与 R-MVP /  后续 PR 的衔接说明