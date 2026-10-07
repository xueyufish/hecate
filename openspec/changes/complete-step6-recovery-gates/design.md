# Design

## Context

Step6 的目标是让本地 Runner、受管 Runner、平台共享执行服务和 durable store 在真实进程与真实存储下保持一致：命令不会重复生效，等待能跨重启恢复，结果未知的外部写不被重做，平台投影只反映宿主事实。Step7 才定义企业审批、策略、撤权和职责分离；Step6 必须先提供保守可执行的技术底座。

## Implementation Order

1. 先补持久命令与新 attempt 关联。统一平台命令记录、宿主命令 inbox、command_id 幂等、effect receipt、过期/重复/参数不匹配拒绝、waiting_input/waiting_approval 唤醒后的新 Run 关联。完成后，取消、输入唤醒和受管命令都能通过同一回执语义表达。
2. 再补内置 continuation 和冻结动作关联。共享执行服务保存不透明 checkpoint 引用、冻结 action id、动作结果引用、身份/部署/版本快照；恢复时优先原生 continuation，不能恢复的受保护动作进入待对账，已完成结果只回放引用。
3. 接真实工作流父子任务。把等待/回调原语接到一个确定性工作流节点或具名 adapter，父任务等待绑定 child ref、workspace、wait token 和期限；回调必须核实子任务真实终态后才唤醒父任务。
4. 补动作边界的保守授权与本地证据门。受管保护动作在实际业务派发点校验当前 lease、scope、nonce 和 expiry；证据不可写或超本地缓冲限制时拒绝新受保护动作。Step7 之前不做中心策略推理，默认 fail-closed。
5. 做安装制品加真实平台 HTTP 验收。以安装后的 Runner wheel、平台 HTTP 服务和真实网络请求验证提交、接受响应丢失、重投、执行、投影、重启、重连、事件补传和命令效果。
6. 做 PostgreSQL 宿主故障矩阵。用 PostgreSQL durable store 验证已接受未执行、执行中 kill、外部写成功但回执丢失、迟到终态、重复命令、多 Run 补传和平台重启。
7. 收口文档和 Step6 完成状态。演进方案只把通过真实验收的条目标为完成；Step7/10 依赖保持独立待办。

## Decisions

- 命令先行，因为 waiting 唤醒、取消、新 attempt 关联、受管命令和工作流回调都依赖同一套 command receipt 语义。
- continuation 在工作流之前完成，因为父子回调和故障矩阵需要已决动作可回放、未知写不重做的基础保证。
- 真实 HTTP/wheel 与 PostgreSQL 放在集成阶段，避免用组件测试提前宣称场景完成。
- Step6 对授权只做可验证的保守门：lease 无效、过期、scope 不匹配、nonce 重放、证据不可写时拒绝；是否“审批合法”归 Step7。

## Non-goals

- 不实现完整 policy engine、企业审批语义、审批职责分离、撤权策略或高风险离线窗口。
- 不实现平台通用 checkpoint 引擎；checkpoint 所有权仍归执行后端，平台只保存引用和恢复状态。
- 不实现 Step10 的中心证据留存、删除、导出、冻结和完整 lifecycle。
- 不引入供应商绑定或要求 Runner 导入平台 ORM。

## Risks / Validation

最大风险是把“命令已记录”误表达为“命令已生效”，或在 continuation/重启中重做结果未知的业务写。验收必须用真实业务 API 调用计数、真实进程 kill/restart、真实 PostgreSQL 和真实 HTTP 请求验证；仅 monkeypatch 或 ASGI 组件测试不能关闭 Step6 门槛。
