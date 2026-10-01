# Proposal

## Why

演进方案 step4(`docs/refactor/enterprise-agent-platform-evolution-plan.md`)的第二支:Change 1(`agent-principal-deployment-model`,#202)已交付身份链契约、Agent principal 与 Deployment 登记表,并显式预留"Run 侧固化由 step4 第二支 task-run-model 消费"。缺少 Task/Run 数据模型时:同一业务任务无法产生可追溯的多次执行尝试(重试、换后端重跑),平台记录与独立宿主记录没有字段所有权边界,独立部署的注册/观察接入没有承载实体,`conversation_id/task_id/run_id/backend_session_id` 无法建立不冒充恢复的映射。step5d(平台入口映射)、step6(持久任务/控制命令)、`managed-runner-enrollment`(受管注册)都以本 change 的模型为前置。

## What Changes

- 新增 `TaskModel`:平台 Task 记录业务目标、发起者(身份链引用)、验收与责任字段、workspace 归属;不承载执行事实。Task 生命周期状态机(queued/waiting_approval 等)属 step6,本 change 不引入。
- 新增 `RunModel`:一次后端执行尝试——绑定 Task 与 Deployment(含固定配置/能力快照引用)、尝试号、身份链快照(消费 `contracts/execution/identity.py` 的 `IdentityChain`)、后端运行 ID 与供应商 session/turn 引用、事件序号游标。重试 MUST 产生新 Run(尝试号递增),MUST NOT 复用旧 run_id 冒充恢复。
- 新增平台/宿主字段分权:Run 的投影字段(平台保存的后端状态投影)与执行事实(执行方权威)显式分列;`origin` 判别平台创建与按来源导入观察;导入观察 MUST NOT 自动取得投递或审批权。
- 新增独立部署注册的模型面(`standalone_enrollments`):宿主身份/信任根校验字段、已安装版本/能力登记、显式的受管新 Run 选择、操作者与审计字段;真实注册/断连/重连验证属 `managed-runner-enrollment`(step6/7)。
- 新增 `conversation_id / task_id / run_id / backend_session_id` 映射服务层:`execution/task_run_registry.py` 单一写入方(禁止业务方双写),一个会话可产生多个任务、一个任务多次尝试;backend session 引用在同一后端域内唯一,不可跨 Run 复用。
- 旧数据兼容:既有 chat session 建立惰性 conversation 映射(不重执行、不改行为);与 Change 1 一致,缺少治理数据登记为待治理。
- 增量迁移:全部为**新表**,不动既有热路径;唯一约束建表即收紧(无存量数据需要回填窗口)。
- 不修改现有执行链路行为;不建 `POST /api/tasks` 等 API(step6);不做受管注册/投影/命令的真实执行路径(step6/7);不迁移活跃 Run。

## Capabilities

### New Capabilities

- `task-run`:平台 Task/Run 的数据契约——Task 责任/验收字段与执行事实的分离、Run 尝试语义与身份链固化、平台/宿主字段所有权、独立部署注册模型面、ID 映射的单写入方规则与旧数据兼容。

### Modified Capabilities

(无——`agent-deployment` spec 不变;本 change 消费其 Deployment/principal 实体与 step3 执行契约类型,不修改其需求。)

## Impact

- **新增**:`src/hecate/models/task.py`、`src/hecate/models/run.py`(含 enrollment/conversation 映射表)、`src/hecate/execution/task_run_registry.py`、contracts 层 Task/Run 逻辑引用类型(`contracts/execution/references.py` 扩展)、alembic 迁移、`tests/test_execution/` 模型与注册服务测试、`tests/test_layering_domain.py` 新表单写入方覆盖。
- **修改**:`src/hecate/models/__init__.py` 导出;方案文档 step4 第 12—17 项勾选。
- **不受影响**:现有 chat/workflow 执行链路(零行为变更)、A2A task_store(映射语义登记,接入属 step8)、step6 API 与 worker、独立宿主本体(step5c)。
- **CI**:新增确定性测试随 pytest 运行,无外部依赖。
