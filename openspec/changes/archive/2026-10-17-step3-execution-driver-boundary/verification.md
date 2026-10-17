# 验证记录 — step3-execution-driver-boundary

本 change 为纯重构：新增 Runtime 内部执行驱动契约并解除共享执行服务对 `PregelRuntime` 的具体类绑定。以下为实施会话的实际验证结果。

## 静态门

- `bash scripts/dev.sh check all`：ruff check 0 错误；ruff format --check 1579 文件已格式化；mypy `src/ packages/ scripts/dev.py` 934 源文件零错误（含 `packages/hecate-runtime/tests/`——包内测试在 mypy 范围内，替身签名按 LSP 校正）。
- `grep -n "Pregel" packages/hecate-runtime/src/hecate_runtime/execution_service.py`：无命中（服务模块零 Pregel 引用）。
- `pregel.py` 零变更（`git diff --name-only` 不含）。

## 核心断言（本 change 的解绑证明）

- **阻断导入环境测试**（`packages/hecate-runtime/tests/test_execution_service.py::test_service_loads_and_drives_stub_without_pregel`）：`sys.modules` 阻断 `hecate_runtime.pregel` 与 `hecate.runtime.pregel` 后，断言 `import hecate_runtime.pregel` 抛 ImportError，`execution_service` 与 `execution_driver` 两个模块仍可导入（无传递性图依赖），并以非图 Stub 驱动完成五类结果分类（成功/异常/协作取消/STOP_UNKNOWN/调度取消）。关键实现事实：`execution_driver.py` 对 pregel 仅做 `TYPE_CHECKING` 注解导入、零运行时依赖，故契约模块与服务模块都可在阻断环境下加载。
- **契约参数化测试**：`PregelExecutionDriver`（真实 `PregelRuntime` + 最小线性图 + Echo worker）与 Stub 驱动各自验证——事件流经共享服务后与直连执行逐事件一致；提前 `aclose()` 终止底层生成器且无遗留后台任务；`resume_value` 经 `_RecordingPregel` 替身（测试内子类，不改 `pregel.py`）与 Stub 双侧断言原样透传。

## 回归矩阵（显式收集数）

| 套件 | 结果 |
|---|---|
| `packages/hecate-runtime/tests/` | **10 passed**（5 既有分类用例迁移至显式 `ExecutionDriver` 子类 + 5 新增） |
| `tests/test_runtime/test_runtime_self_sufficiency.py` + `test_kernel_purity.py` + `tests/test_layering_domain.py` | **23 passed**（自足探针与分层零破坏） |
| `packages/hecate-runner/tests/`（全套） | **237 passed**（含持久审批、worker 重启恢复、断连场景——Runner 经 `_assemble_runtime` 返回驱动的路径） |
| `tests/test_execution/`（49 文件全套） | **626 passed, 42 skipped, 1 flake**（见下） |
| `tests/test_channel/test_loop_stop_semantics.py` + `tests/test_layering_entry_imports.py` + `tests/test_api/test_chat_regression.py` | **42 passed**（聊天引擎路径与入口分层——`AssembledExecution.runtime` 保留供其直驱） |

## 实施中发现并处理

1. **第三消费者（design D3 修正）**：`AssembledExecution.runtime` 除 builtin 外还被 studio 聊天执行服务直接驱动（含 Pregel 特有 `snapshot_at_version`）。按原 D3 把字段改为 `ExecutionDriver` 会破坏聊天路径；把 `snapshot_at_version` 提进契约会违反"不纳入图私有语义"。已修正 design.md D3 并实施：`AssembledExecution` 保留 `runtime`、新增 `driver` 字段（组装处 `PregelExecutionDriver` 包装一次），builtin 改传 `assembled.driver`，聊天路径零改动。
2. **测试 fixture 更新**：`tests/test_execution/test_builtin_backend.py` 的 `assemble_execution` monkeypatch 替身补 `driver` 字段；该测试的 driver 导入用 `hecate_runtime.execution_driver`（新内核模块不新增 shim，遵守"新代码不指向 shim"规则）。
3. **mypy 覆盖包内测试**：`packages/**/tests` 在 mypy 范围内，替身类签名按基类 LSP 校正（`_RecordingPregel`/`_BlockedStub` 显式参数）。
4. **既有 flake（非本 change 引入，已实验证明）**：`tests/test_execution/test_approval_wait_park.py` 整文件运行偶发 1-2 例失败（`test_terminal_run_is_backfilled_despite_old_consumed_wait`、`test_authority_rejects_clock_rollback_on_restart` 等，时序敏感）。验证：`git stash` 清空本 change 全部改动后同文件运行复现失败（2 failed），恢复改动后运行通过（25 passed）；隔离单测多次通过。属既有时序 flake，建议另行立项（引入 pickle/pinning 时钟或降低文件内耦合）。

## 未完成项与保留限制

- 本 change 不实现 Controller 循环、不新增决策/存储语义、不迁移聊天路由、不翻默认开关；`resume_value` 的 Pregel 解释（checkpoint 恢复）与 step6d 现状一致，无新增恢复保证。
- `ExecutionDriver` 契约当前只有 Pregel 与测试替身两个实现；Controller 驱动为后续 change。
- 阻断导入测试为本仓内证明；生产组合中的实际 Controller 接入验收归 step5e/6/7 所属 change。
