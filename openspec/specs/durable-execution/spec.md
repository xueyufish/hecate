# durable-execution Specification

## Purpose

持久执行的核心正确性语义:失效派发经租约 fencing 不覆盖新派发,outbox 事件投递不漏读晚提交记录,持久动作领取与事件日志保持一致,宿主能力声明与重启恢复如实反映实际边界。这些需求钉住 durable 存储、worker 与宿主装配之间的回归契约。

## Requirements

### Requirement: 失效派发不可覆盖新派发

租约过期后的同名 worker 重获租约 MUST 生成新 fencing token。续租、释放与任务结果 MUST 绑定代次；旧回调的成功、等待及异常不得写入新派发状态。

#### Scenario: 同名持有者重新领取

- **WHEN** 旧租约已释放或过期，原 holder 再次领取
- **THEN** 新 token 递增；原 token 的续租、状态修改及释放被拒绝

#### Scenario: 平台实际入口返回迟到成功

- **WHEN** successor 已接管任务，旧 PlatformTaskDispatcher 返回执行结果
- **THEN** 旧结果不可更新当前任务状态或产生成功终态

### Requirement: 事件投递不可漏读晚提交事件

outbox 接收确认 MUST 以 relay 与 event 为键持久化。晚提交且 ID 小于已投递事件的记录仍 MUST 被发现；投影冲突不能未经核验当作成功。

#### Scenario: PostgreSQL 提交顺序与序列顺序相反

- **WHEN** 较高 ID 事件先提交并投影，较低 ID 事务随后提交
- **THEN** 下次中继仍投影该事件；重启后已确认事件不再次作为待投递

### Requirement: 持久动作领取与事件日志一致

已获得 SQL Action 领取的执行者 MUST 不把自己的领取当作历史崩溃。共享装配 MUST 将持久账本注入真实 ToolWorker，保持原逻辑动作身份恢复结果。

#### Scenario: 首次执行及持久结果回填

- **WHEN** 同一工具调用先执行成功，再以空 EventStore 和新 hook 恢复
- **THEN** 实际业务工具只调用一次，恢复返回已保存的真实结果

### Requirement: 宿主能力声明及恢复诚实

durable 能力 MUST 不被预览默认项覆盖。重启恢复 MUST 保留可信业务身份及域；受管上传 MUST 不用一个 Run 的 source_sequence 跳过其他 Run 的事件。未接通的托管配置 MUST 显式拒绝启动。

#### Scenario: 已撤销身份的待恢复任务

- **WHEN** 持久身份不再符合当前 profile 信任配置
- **THEN** 任务进入待对账，不采用其他身份执行或扩大请求体自报权限

#### Scenario: 多 Run 事件补传

- **WHEN** 本地各 Run 都从低序号产生事件并重新上传
- **THEN** 每个 Run 各自补传，上游去重且旧事件不回滚终态
