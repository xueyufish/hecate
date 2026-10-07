# Tasks

## 1. 回调契约校验

- [x] 1.1 `task_control.py`:`submit_workflow_callback`——父等待契约的 `await_task_ref` 匹配、子任务同 workspace、终态与声明一致校验;通过后并入子结果摘要并复用唤醒应用;各失败分支显式 `rejected` 且不消费 token。验证:单测覆盖正/各负例。

## 2. REST 回调入口

- [x] 2.1 `tasks.py`:`POST /tasks/{task_id}/workflow-callback`(workspace 认证,请求模型绑定 command_id/wait_token/child_task_id/declared_state/result)。验证:API 测试覆盖认证、回执形状、问题文档错误。

## 3. 验收测试

- [x] 3.1 正链:子任务 succeeded → 回调 → 父 requeue 含子结果摘要 → 回执 applied;重复回调幂等。验证:集成测试通过。
- [x] 3.2 负例:伪造子引用、跨 workspace 子引用、状态不符、迟到回调(token 已消费/过期)——全部显式拒绝且父任务保持等待、token 未消费。验证:集成测试通过。
- [x] 3.3 父子分别重启:父等待与子终态跨持久重启保持,回调按正常语义应用。验证:集成测试通过(持久状态模拟进程死亡)。
- [x] 3.4 既有回归:test_execution 全量。验证:scoped pytest。

## 4. 验证与文档

- [x] 4.1 演进方案 step6e 状态标注(回调入口已交付、工作流图节点消费归后续切片)。验证:文档与代码一致。
- [x] 4.2 ruff check、ruff format --check(CI 同款范围)、mypy src/ packages/;受影响 pytest 全绿。验证:本地门禁零错误。
