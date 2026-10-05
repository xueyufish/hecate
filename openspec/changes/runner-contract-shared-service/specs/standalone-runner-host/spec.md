# Spec Delta

## ADDED Requirements

### Requirement: 正式执行后端 HTTP 契约绑定

独立宿主 MUST 按 `execution-backend.http.v0_1` 语义暴露能力声明、提交、状态、事件游标、取消与 artifact 引用;正式请求和响应 MUST 通过权威 JSON Schema 校验。宿主 MUST 仅依赖随独立发行物发布的 schema/样本语义,不要求导入 Hecate Python 对象或查询平台 ORM。提交 MUST 绑定 transport 校验出的 subject、header/body 一致的 idempotency key 和 canonical request digest;同 key 同请求返回原 backend run,异请求返回版本冲突。

#### Scenario: 公开协议提交与读取

- **WHEN** 非 Python 客户端以权威 `ExecutionRequest` 调用 `/runs`
- **THEN** 获得结构化 backend `run_ref`,并可用该引用查询 `RunStatus`、`EventPage`、cancel receipt 和 artifact 引用

#### Scenario: 幂等重试不重复执行

- **WHEN** 提交响应丢失后以同一 header/body idempotency key 重试
- **THEN** 返回原 backend run,不创建第二个本地 task/run,不重复业务动作

### Requirement: 执行事件持久化并支持重启后查询

启用 durable profile 的宿主 MUST 将本地执行事件映射为 governance event envelope 并写入本地 durable event log;event id 与 source sequence MUST 在重放中稳定,重复相同事件 MUST 幂等,同一位置不同内容 MUST 冲突并进入待对账语义。宿主重启后 MUST 能按 backend run ref 查询已持久化状态与事件游标,且查询授权基于提交时记录的 server-verified subject。

#### Scenario: 重启后查询终态与事件

- **WHEN** durable Run 已到终态后宿主进程重启,原 caller 以结构化 run ref 查询
- **THEN** 状态与事件来自本地 durable 事实,事件游标可续读且不依赖进程内 state

#### Scenario: 重放事件幂等

- **WHEN** 恢复重放生成同一 run 位置与内容的执行事件
- **THEN** event log 不新增重复事件或 sequence,后续游标读取不重复投递
