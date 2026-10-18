# Verification

## 受影响测试

Windows worktree 的 `.venv/Scripts/python.exe` 直接调用仓库共享开发 CLI，与 `bash scripts/dev.sh` 使用同一检查实现和 worktree 环境。

```text
.venv/Scripts/python.exe scripts/dev.py test packages/hecate-runtime/tests tests/test_core/test_port_decision_responder_adapter.py tests/test_core/test_runtime_port_cost.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_runtime/test_kernel_purity.py tests/test_layering_domain.py tests/test_runtime/test_llm_worker.py -q
143 passed in 22.82s
```

显式收集 package 测试，不依赖默认只收集 tests/ 的 testpaths。范围覆盖 responder、既有决策接受/恢复、共享执行服务、实际生产端口转换、费用记录、LLM worker 和架构约束；无跳过。

## 共享检查

```text
.venv/Scripts/python.exe scripts/dev.py check all
ruff check: All checks passed!
ruff format --check: 1589 files already formatted
mypy: Success: no issues found in 942 source files
```

复核过程中新增测试的两处类型推断问题已通过显式注解修复，最终检查为零错误，没有类型忽略或降低 gate。

## 规范与范围

```text
openspec validate step6-runtime-port-decision-responder --strict
Change 'step6-runtime-port-decision-responder' is valid
git diff --check
exit 0
```

变更文件尚未跟踪，额外对 change artifacts 及新增代码执行逐行空白检查；没有尾部空白。实际差异只在本 change 的模块、测试与 artifacts。无 schema 迁移、无 PostgreSQL 认证要求；真实生产端口集成测试采用既有 SQLite fixture 和确定性底层模型服务，不构成真实模型质量验收。

原实现补充回归时复现 23 个失败用例；最终修复与证据映射见 review.md。CI、PR review 未在本地替代执行。
