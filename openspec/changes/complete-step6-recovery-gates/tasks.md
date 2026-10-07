# Tasks

- [x] 1. 完成持久命令闭环：平台命令记录、宿主命令 inbox、command_id 幂等、effect receipt、过期/重复/参数不匹配拒绝、waiting 唤醒后的新 Run/attempt 平台关联。
- [x] 2. 完成内置 continuation：冻结 action id、checkpoint 引用、已决结果回放、未知写待对账、完成结果 replay 和取消/中断命令的真实效果回执。
- [x] 3. 接入真实工作流父子等待：实现确定性 workflow 节点或具名 adapter，校验 child ref/workspace/terminal state/wait token/期限，覆盖父子进程重启。
- [x] 4. 完成动作边界保守门：受管保护动作派发点校验 lease/scope/nonce/expiry，证据不可写或缓冲超限时拒绝新受保护动作，使用业务 API 调用计数验证零副作用。
- [x] 5. 完成安装制品与真实平台 HTTP 验收：Runner wheel 对接平台 HTTP，覆盖接受响应丢失、重投、执行、状态投影、宿主重启、重连和多 Run 事件补传。
- [ ] 6. 完成 PostgreSQL 宿主故障矩阵：覆盖已接受未执行、执行中 kill/restart、外部写成功但回执丢失、迟到终态、重复命令、平台重启和宿主失联。
- [x] 7. 更新演进方案与复核报告：只把通过真实验收的 Step6 条目标为完成，明确剩余 Step7/10 依赖和不再属于 Step6 的事项。
- [x] 8. 运行验证：Ruff check/format、`mypy src/ packages/`（892 个源文件）、OpenSpec strict 校验及受影响 pytest（53 passed）均通过。PostgreSQL 宿主故障矩阵属于 Task6，仍未通过，详见 `docs/refactor/step6-followup-review.md`。
