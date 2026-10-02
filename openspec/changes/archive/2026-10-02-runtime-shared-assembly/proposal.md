# Proposal

## Why

演进方案 step5a（§五/§七首轮拆分表 `runtime-shared-assembly`）要求在内置 Runtime 之上建立两层目前不存在的东西：**(1) 可被平台与未来独立宿主共用的共享执行装配**，**(2) step3 `AgentExecutionBackend` 契约的首个真实实现**。当前 `WorkflowExecutionService`（studio/workflows/execution_service.py,1325 行）把装配逻辑与平台职责揉在一起——模块级 import `WorkflowModel/WorkflowVersionModel`(:24)、`AgentModel` 查询(:391/:419)、`execution_mode` 读取(:884/:888)、版本解析(:935/:942)——独立消费基线 §2 已把该链段登记为"待解除耦合,责任 step 5a"。契约侧目前只有 `StubExecutionBackend` 与非 Python 试点两个验证者,尚无内置后端;后续 step5b 的 `hecate-runtime` 发行包抽取、step5c 宿主都以本 change 的装配边界为直接输入。方案红线:只包装旧服务不算独立消费完成证据;平台查询不得进内核。

## What Changes

- **抽取共享执行装配**:图编译、Worker 构造、上下文链、guardrail 装配逻辑从 `WorkflowExecutionService` 抽为共享装配函数与执行应用服务,放进 `runtime/` 域(不依赖 studio/ORM);`WorkflowExecutionService` 保留为平台 adapter——定义解析(Agent/Workflow/Version ORM 查询)、平台授权映射留在其中,转换为执行输入后调用共享装配。公共 API 与既有入口行为不变。
- **新增 `HecateExecutionBackend`**(建议 `src/hecate/execution/builtin.py`):实现 `AgentExecutionBackend` 六方法,包装共享装配(不是绕过它另写一套);提交幂等(同键同内容返回原回执,异内容 version_conflict);引擎状态如实映射(不伪报成功);`request_cancel` 只回 REQUESTED 级回执、绝不报 APPLIED(基线 N1:平台级取消 API 不存在);引擎事件映射为契约 `EventEnvelope` 游标页,原始事件作后端详情保留;可选能力(pause/resume/provide_input/resolve_approval/export_context)显式声明 unsupported。
- **同步-异步桥**:契约方法是同步语义(为进程外 HTTP 绑定设计),内置实现以事件循环调度执行、回执先落 PENDING,状态经 `get_run` 观察;进程内单事件循环假设成文。
- **兼容样本钉住**:为内置后端的请求/回执/状态/事件/取消补充标准样本(沿用 `tests/test_execution/samples/` 机制),供后续切片对比行为漂移。
- **分层守卫扩展**:装配模块禁止 import studio/ORM(并入既有 layering/AST 测试);内置后端禁止查询平台 ORM(契约"接收方不查平台 ORM"落到进程内)。
- **不做**:包抽取与 wheel(5b)、独立宿主(5c)、平台入口迁移与 G3(5d)、取消的强制执行与持久化 run 记录(step6)、审批回调经契约暴露(后续切片)。

## Capabilities

### New Capabilities

- `builtin-execution-backend`:内置执行后端的契约实现与共享装配边界——六方法行为的如实性(幂等、状态、取消回执、事件游标)、共享装配的平台无关性(ORM/定义查询不得进入)、包装与平台路径共用同一装配(行为一致)、兼容样本钉住。

### Modified Capabilities

(无——`execution-backend-contract` 本身不变,本 change 是其首个内置实现;`pregel-runtime`/`runtime-pluggability` 行为不受影响。)

## Impact

- **修改**:`src/hecate/studio/workflows/execution_service.py`(瘦身,公共 API 不变);`tests/test_layering_domain.py`(装配 import 守卫)。
- **新增**:`src/hecate/runtime/` 下装配模块(名称 design 定);`src/hecate/execution/builtin.py`;契约测试对内置后端的参数化复用(`tests/test_execution/test_backend_contract.py`);builtin 标准样本。
- **不动**:API 契约、数据库 schema、既有场景包断言(S01—S11 继续全绿即行为兼容证据);`core/composition/` 装配根仅跟随新函数调整调用。
- **风险**:1325 行服务的重构面——以"现有测试不改断言全绿"为硬门;旧入口回归由既有 chat/workflow/evaluation 测试覆盖,CI 全量兜底。
