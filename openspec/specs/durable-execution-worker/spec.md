# durable-execution-worker Specification

## Purpose

持久执行 worker 参考实现(hecate-durable `worker` 模块):以租约/fencing 认领持久任务并投递给注入的执行回调,提供重启对账、有界重试、outbox 中继投影与独立进程入口。平台任务控制面与 runner durable profile 共用同一 worker 库,使"任务不依赖 HTTP 请求存活、重启后仍知道任务在哪、哪些命令未确认"跨进程成立。

## Requirements

### Requirement: worker 以租约与 fencing 认领排队任务并真实投递

worker SHALL 周期扫描 `queued` 任务,经租约管理器按资源键原子认领(holder、expires_at、单调 fencing_token),并在租约有效期内调用注入的 dispatcher 执行;同一任务在同一时刻 SHALL 至多被一个 worker 实例认领。dispatcher 为注入接口:平台绑定任务控制服务的执行回调,runner 绑定其引擎派发;worker 库 MUST NOT import 平台或宿主代码。

#### Scenario: 并发认领唯一

- **WHEN** 两个 worker 实例并发扫描同一 `queued` 任务
- **THEN** 至多一个实例认领成功并投递执行,另一个跳过该任务

#### Scenario: dispatcher 注入且无主应用依赖

- **WHEN** 平台以任务控制服务的执行回调装配 worker
- **THEN** worker 经回调完成投递,核心包依赖闭包不包含 `hecate` 主应用

### Requirement: 重启对账不重复、不丢失

worker 启动与周期对账 SHALL:`queued` 任务重新进入认领;租约过期的在途任务以新 fencing token 重新认领并重派;持活跃租约的任务保持原状不重派。重派结果经提交幂等键与动作台账仲裁:同键结果已存在则回填不重执行,动作类副作用按台账四态决策(已成功回填真实结果引用、claimed 写入安全停止、结果未知进入待对账),不盲目重做外部副作用。

#### Scenario: worker 崩溃后任务继续

- **WHEN** worker 在任务执行中崩溃且租约过期
- **THEN** 另一 worker 以新 fencing token 重派,该任务最终到达终态或显式 `reconciliation_required`,不静默丢失

#### Scenario: 已完成任务不重复执行

- **WHEN** 重启对账遇到已有终态结果的任务
- **THEN** 回填既有结果关联,不创建第二次同义执行

### Requirement: 投递重试有界且放弃显式

派发失败 SHALL 按次数上限与退避策略重试;超过上限的任务 SHALL 进入 `reconciliation_required` 并保留失败事件,不得无限重试、不得静默丢弃。优雅 drain SHALL 停止接受新认领并等待在途任务完成或租约到期,关停报告如实反映在途状态。

#### Scenario: 超限进入待对账

- **WHEN** 派发连续失败达到次数上限
- **THEN** 任务状态为 `reconciliation_required`,事件日志记录失败原因,此后不再自动重试

#### Scenario: drain 如实报告

- **WHEN** 关停请求到达时存在在途任务
- **THEN** worker 拒绝新认领,等待在途完成或如实报告未完成,不宣称已全部完成

### Requirement: outbox 中继按游标有界投影

seam 状态写入同事务追加的治理事件 envelope SHALL 作为权威 outbox;中继 SHALL 按游标读取事件日志,以有界重试投影到平台读模型(`platform_events`),同一 event_id 不得重复投影;投递重试与重放有界,慢消费经游标恢复;中继失败 MUST NOT 阻塞状态写入,MUST NOT 丢失事件,读取端缺口显式标记。

#### Scenario: 中继失败后补投不丢不重

- **WHEN** 读模型短暂不可用后恢复
- **THEN** 事件保留在 outbox,恢复后按游标补投,顺序与去重保持

#### Scenario: 中继故障不阻塞状态

- **WHEN** 投影目标持续失败
- **THEN** 任务状态迁移照常提交并进入事件日志,读取端缺口显式可见

### Requirement: worker 可作为独立进程运行

worker SHALL 提供独立进程入口:与平台或宿主共用同一持久化存储,经引导装配注入 dispatcher 后运行完整的派发/对账/drain 循环;启动失败(存储不可达、装配缺失)以非零退出并指明原因,不进入空转。进程内 asyncio 循环(多实例经租约安全并存)与独立进程两种运行方式 MUST NOT 产生派发语义差异。

#### Scenario: 独立进程执行平台任务

- **WHEN** 以 worker 入口与平台共用数据库启动,平台提交一个 `queued` 任务
- **THEN** worker 认领并执行该任务,状态与事件结果与进程内运行一致

#### Scenario: 启动失败如实退出

- **WHEN** 存储不可达时启动独立 worker 进程
- **THEN** 进程以非零退出并指明原因,不提供服务也不空转
