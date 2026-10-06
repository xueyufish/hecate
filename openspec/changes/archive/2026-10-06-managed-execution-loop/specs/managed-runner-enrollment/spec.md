# Spec Delta

## ADDED Requirements

### Requirement: 平台投影仅在宿主事件后进入执行状态

受管投递的接受与确认回执 SHALL 只表达投递对账事实(已接受),不表达执行状态;平台 Run 投影的 `queued`/`running`/终态 SHALL 仅由宿主上传的实际执行事件(`task_state`/`run_terminal`)折叠产生。宿主终态迁移 SHALL 上传携带结果载荷(status/error/content)的 `run_terminal` 事件,使平台投影获得真实终态与错误信息;旧序号或迟到的宿主事件 MUST NOT 回滚已折叠的终态投影。

#### Scenario: 接受后平台投影仍无执行状态

- **WHEN** 宿主已接受投递并向平台确认,但尚未开始执行
- **THEN** 平台投递记录为已接受,平台 Run 投影不进入 running 或任何执行状态

#### Scenario: 终态经 run_terminal 投影且不回滚

- **WHEN** 宿主上传 `run_terminal`(含 status 与错误)后,又收到旧序号事件
- **THEN** 平台投影呈现真实终态与结果载荷,旧序号事件被忽略,终态不被重开

### Requirement: 重启后按原 Task/Run 恢复且补传不跨 Run 跳过

宿主进程重启后,已接受未执行的受管任务 SHALL 恢复为同一本地 Task/Run 并继续执行,不新建执行、不重复已完成的业务写入(动作台账把关);持久化事件中未上传的部分 SHALL 在重连后按 Run 独立游标补传——补传任一 Run 的事件 MUST NOT 跳过或丢失其他 Run 的事件,重复上传由平台按 event_id 去重吸收。

#### Scenario: 接受后宕机重启恢复同一 Task/Run

- **WHEN** 宿主接受受管投递并持久化后进程终止,重启后调度循环恢复
- **THEN** 该任务以原本地 Task/Run 标识被驱动至终态,业务副作用仅发生一次

#### Scenario: 重连补传多 Run 事件互不跳过

- **WHEN** 两个受管 Run 在断线期间各自产生事件,宿主重启后重连上传
- **THEN** 两个 Run 的全部事件按各自游标完整补传,平台去重后无丢失、无重复投影
