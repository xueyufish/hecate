# Tasks

## 1. harness 扩展

- [x] 1.1 `runner_harness`:durable profile 构造(写/审批工具、持久库、`data_domains`)、`RunnerInstance.stop(kill=True)` 硬终止、`restart()`(同 profile/持久库新子进程)、业务 API 计数端点透出。验证:harness 自检测试(现有模式)。

## 2. 场景实现

- [x] 2.1 SC03 进程级:批准写入执行一次 → 硬终止 → 重启 → 已决回填不重做(计数断言)、等待合法唤醒、未知结果不重写。验证:`tests/scenarios/test_sc03_local_approval_restart.py`。
- [x] 2.2 SC06 进程级:不可写证据 → 保护动作停止(引擎全量 fail-closed;readonly-continuation 为登记缺口)→ 恢复后保护动作恢复。验证:`tests/scenarios/test_sc06_evidence_failure.py`。

## 3. 清单登记

- [x] 3.1 `manifest.yaml`:SC03/SC06 登记 `implemented_slices`(按仓库规则保持 planned 状态,部分覆盖保留验收门);SC04/SC05 增补组件切片与进程级缺口说明。验证:清单一致性测试通过。

## 4. 验证与文档

- [x] 4.1 演进方案 step6f 状态标注(SC03/SC06 进程级交付,SC04/SC05 受管进程级与 PostgreSQL 参考矩阵登记缺口)。验证:文档与代码一致。
- [x] 4.2 ruff check、ruff format --check(CI 同款范围)、mypy src/ packages/;场景与受影响 pytest 全绿。验证:本地门禁零错误。
