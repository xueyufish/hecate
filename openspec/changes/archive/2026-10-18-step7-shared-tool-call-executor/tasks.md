# Tasks

## 1. 提取 ToolCallExecutor

- [x] 1.1 新增 `packages/hecate-runtime/src/hecate_runtime/tool_call_executor.py`：`ToolCallExecutor` 具体类（构造参数按 design D1；自持 `event_store`，不继承 Worker）；迁入单链（公共入口 `execute_call`，原 `_execute_single_tool` 本体）与归属表内全部私有方法（`_recovery_outcome`、`_log_recorded_content`、`_hook_resolution`、`_hook_claim`、`_mirror_outcome`、`_checked_recovery_result`、`_check_access`、`_withheld_result`、`_capture_evidence`、`_emit_channel_write_rejected`、`_append_tool_call`）及模块级恢复函数（`_is_indeterminate_error`、`get_tool_receipt`、`_merge_resolutions`）；类 docstring 记录 provenance 契约（D3）。验证：模块可导入；`grep -n "class ToolCallExecutor" + 无 ABC 基类`；对每个迁出方法在 tool_worker 批次路径 grep 引用归零（归属表核对，发现表外调用点按准则归类并记录）。
- [x] 1.2 `workers/tool_worker.py` 改委托：`__init__` 构造 `self._executor`（治理字段转存，签名不变）；`_execute_single_tool` 一行委托；批次路径（`execute`、`_extract_tool_calls`、`_apply_citation_provenance`）不动；模块级恢复函数改为从新模块 re-export（兼容导入不变）。验证：`grep -n "class ToolWorker" 仍继承 Worker`；`test_g2_recovery_states.py:488` 直呼路径不改测试即通过；`git diff` 确认既有图上下文路径语义不变；独立调用 superstep 缺省另有回归。

## 2. 测试

- [x] 2.1 新增 `packages/hecate-runtime/tests/test_tool_call_executor.py`：fixture 参数化 executor / worker 双入口；行为用例覆盖策略拒绝、审批拒绝、无 callback、middleware 链 BLOCK/SANITIZE、legacy hook、sandbox 路由、成功、异常（RECEIPT_FAILED）、未知结果（`ExecutionStoppedError`）、账本 claim 与已完成动作真实回填（不重复执行）。验证：双入口全部通过且逐项一致，报告实际用例数。
- [x] 2.2 新增禁 Pregel 导入测试：阻断 `hecate_runtime.pregel`（自足探针同款机制）后导入 `tool_call_executor` 并以 fake port 完成一次只读调用。验证：通过且无图执行机制导入；如发现既有模块传递导入 pregel，如实记录范围（不越界解耦无关模块）。

## 3. 回归与收尾

- [x] 3.1 显式收集运行：`python -m pytest packages/hecate-runtime/tests -q` + `tests/test_runtime/test_g2_recovery_states.py tests/test_runtime/test_path_consistency.py tests/test_runtime/test_worker_chain_integration.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_layering_domain.py tests/scenarios/tools/receipt_harness.py tests/test_execution/test_approval_wait_park.py`。验证：全过（不改既有断言），报告实际收集数；任何失败均核实原因并记录，跳过不计通过。
- [x] 3.2 运行共享检查 `bash scripts/dev.sh check all`。验证：0 错误。
- [x] 3.3 汇总 `verification.md`：双入口用例数、禁 Pregel 证明、既有套件回归数、归属表偏差记录（如有）、边界声明（无决策派发/无 Controller）。验证：验证记录含实际结果；tasks 全部勾选；PR/CI 由后续提交流程完成。

## 4. 复核修正

- [x] 4.1 修正独立调用的 superstep 缺省行为，移出 sandbox router 的隐式图依赖并保留旧路径转发；独立进程验证无图执行。
- [x] 4.2 双入口通过真实 Worker 批次入口，补 middleware、未知结果、账本恢复/故障、provenance、并发及异常排空证据；更新既有测试的内部装配访问，保留原行为断言。
- [x] 4.3 运行相关回归与共享检查，按实际结果修正规范、设计及 verification，记录复核结论。
