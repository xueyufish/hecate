# Tasks

## 1. 契约与 Pregel 驱动

- [x] 1.1 新增 `packages/hecate-runtime/src/hecate_runtime/execution_driver.py`：`ExecutionDriver`（`abc.ABC`，抽象 `execute(session_id, initial_input, execution_mode, stream_mode, resume_value)` 返回异步事件迭代器，docstring 英文、说明 `resume_value` 为不透明恢复负载、契约服务于内置 Controller 与 Workflow/Pregel 驱动、不面向外部 Runtime）；`PregelExecutionDriver` 组合包装 `PregelRuntime` 并原样转发参数与事件流（不二次包装生成器）。验证：文件存在，`pregel.py` 零变更（`git diff --name-only` 不含 pregel.py），`ruff`/`mypy` 通过。
- [x] 1.2 解绑 `execution_service.py`：删除 `from hecate_runtime.pregel import PregelRuntime`，`RuntimeExecutionRequest.runtime` 改为 `ExecutionDriver`；事件观察、协作取消、`STOP_UNKNOWN` 提前 `aclose`、调度取消包装与四类结果分类逻辑零改动。验证：`grep -n "Pregel" packages/hecate-runtime/src/hecate_runtime/execution_service.py` 无命中；模块 docstring 同步更新。

## 2. 消费者迁移（组装点包装）

- [x] 2.1 共享装配：`packages/hecate-runtime/src/hecate_runtime/execution_assembly.py` 的组装产物新增 `driver: ExecutionDriver` 字段（组装处构造 `PregelExecutionDriver` 包装一次；`runtime: PregelRuntime` 保留——实施发现 studio 聊天执行服务直接驱动 runtime 含 Pregel 特有 `snapshot_at_version`，见 design D3 修正）；builtin 共享服务调用点改传 `assembled.driver`。验证：`mypy` 通过——builtin 若直接传裸 `PregelRuntime` 会类型报错；聊天路径（studio/workflows/execution_service.py）零改动。
- [x] 2.2 Runner：`packages/hecate-runner/src/hecate_runner/engine.py` 的 `_assemble_runtime` 返回 `PregelExecutionDriver(PregelRuntime(...))`，调用点不变。验证：Runner 包 `mypy` 通过；`grep -n "runtime=self._assemble_runtime" packages/hecate-runner/src/hecate_runner/engine.py` 处传出的已是驱动。

## 3. 测试

- [x] 3.1 现有替身迁移：`packages/hecate-runtime/tests/test_execution_service.py` 的 `_Runtime` 改为显式 `ExecutionDriver` 子类（行为与断言不变）。验证：该文件在根目录 `pytest` 下全绿，断言零放宽。
- [x] 3.2 新增环境测试：`sys.modules` 阻断 `hecate_runtime.pregel`（及 `hecate.runtime.pregel` shim 名，参照 `tests/test_runtime/test_runtime_self_sufficiency.py` 机制）后导入 `execution_service` 并以非图 Stub 驱动执行，断言五类结果分类（成功/异常/STOP_UNKNOWN/协作取消/调度取消）与阻断前一致。验证：测试通过且模块未触发 Pregel 导入（阻断生效断言）。
- [x] 3.3 新增契约参数化测试：同一组用例（事件逐个转发、`aclose` 提前关闭传播、`resume_value` 原样透传）分别以 `PregelExecutionDriver`（真实 Pregel 实例 + 最小图或既有测试图）与 Stub 驱动运行。验证：两组全绿，提前关闭后无遗留后台任务（断言生成器终止）。

## 4. 回归验证与收尾

- [x] 4.1 显式收集全部相关测试（根 `testpaths` 只含 `tests/`，不能漏包内测试）：`python -m pytest packages/hecate-runtime/tests/test_execution_service.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_layering_domain.py -q`，Runner 侧 `python -m pytest packages/hecate-runner/tests -q`，builtin 侧执行/审批/恢复相关目录（按 `tests/test_execution/`、`tests/test_channel/` 中触及 `runtime_execution_service` 或审批等待的文件）。验证：全部通过；报告实际收集用例数（不只说 passed）。
- [x] 4.2 运行共享检查 `bash scripts/dev.sh check all`（ruff/format/mypy 全仓）与 Runtime 自足相关测试。验证：0 错误。
- [x] 4.3 汇总实际验证结果到本 change 的 `verification.md`：各命令输出要点、收集数、未完成项与保留限制（不宣称新增恢复保证）。验证：PR 描述含实际结果；tasks 全部勾选。
