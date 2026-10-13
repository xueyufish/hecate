# Tasks

## 1. 终态回写与错误/产物契约映射

- [x] 1.1 `TaskRunRegistry` 终态写入方法(成功:终态+结果摘要;失败:终态+停止类别+安全摘要),走 `update_projection` 通道,单写者不变;单测覆盖成功/失败/安全摘要截断与类别透传
- [x] 1.2 `EntryExecutionService.execute` 包裹执行:停止类异常→失败终态(类别映射);内部异常→generic 失败终态(稳定安全文案);成功→成功终态+结果摘要;关联缺失时跳过回写且不改变既有 fail-open 语义;单测覆盖三类路径与"回写失败不影响已交付响应"
- [x] 1.3 聊天非流式:停止 409 payload 与 `hecate_correlation` 携带 task_ref/run_ref(已登记时);内部异常路径同样先回写再抛出;测试断言契约面只有类别+引用+安全摘要
- [x] 1.4 聊天流式:SSE 终止事件携带关联引用;正常流的首个事件(或响应头)暴露 Task/Run 引用,与非流式对称;测试覆盖正常/停止两形状的引用可见性
- [x] 1.5 A2A 执行器回归:入口服务终态回写后 A2A 失败 Task 的投影同步;稳定文案与不泄露断言保持

## 2. 信封持久化与同步/流式同视图

- [x] 2.1 `RunEventMapper` 信封 sink:追加到所装配 `EventStore`(run 作用域键 `run:{run_ref.id}`、CUSTOM 类型、payload 携带信封);分页读取先查存储、回退进程内保留;单测覆盖追加与分页(内存后端)
- [x] 2.2 流式路径接线:映射出的每个信封经 sink 落库;持久后端下的跨请求可读性以可替换存储双实现的单测验证(内存后端验证 sink 调用,持久语义由键序/追加断言覆盖;Postgres 专项条件跳过沿用既有模式)
- [x] 2.3 同步路径对等:`_non_stream_execute` 收集排空的引擎事件,入口服务经同一映射器+sink 落信封;客户端响应不携带引擎事件;测试断言同一请求两形状的信封可分页且配对校验一致
- [x] 2.4 既有信封/分页/配对回归:entry_events 既有测试全绿,新增不破坏内存保留回退

## 3. 定时 workflow 收口与 A2A 解析

- [x] 3.1 `WorkflowExecutor` 重写为 `EntryExecutionService` 路径(agent_mode=workflow、创建者 workspace/user 关联、result_summary 绑定不变);测试:定时 workflow 执行登记 Task/Run、事件经入口路径产生
- [x] 3.2 `WorkflowTestRunner` 收缩确认:仅 studio 测试端点消费;分层/引用检查定时路径不再 import;studio 测试端点行为回归不变
- [x] 3.3 A2A 默认部署解析:单 agent 沿用、多 agent 恰一默认部署解析、无/多默认部署拒绝、作用域外不可选;拒绝文案稳定;测试覆盖四个场景
- [x] 3.4 A2A 文档更新:`enable-a2a-server.md` 与 `env-vars.md` 补默认部署解析语义

## 4. 支持矩阵与收尾

- [x] 4.1 `docs/design/entry-support-matrix.md`:入口×消费模式矩阵 + 证据指针 + 诚实缺口;与 `execution-stack-compatibility-matrix.md` 交叉引用;遵守 writing-style
- [x] 4.2 演进计划 step5d 剩余两项更新为本片交付状态与剩余缺口(引用矩阵与各 spec)
- [x] 4.3 全门槛:`ruff check`、`ruff format --check`、`mypy`、触及面 pytest(入口契约/信封/定时/A2A/studio 回归)全绿
- [x] 4.4 复核记录:未验证项(A2A 调用方映射、独立/受管宿主的入口认证、Postgres 专项条件跳过范围)如实声明,不冒充完成
