# Tasks

任务组 1 对应 PR1(装配抽取,行为零变化),任务组 2—3 对应 PR2(内置后端与样本);组 4 收尾。每组完成即勾选。

## 1. 共享装配抽取(PR1,行为零变化)

- [x] 1.1 新建 `src/hecate/runtime/execution_assembly.py`:从 `WorkflowExecutionService` 抽出图编译、Worker 构造、上下文链、guardrail 装配为纯函数/显式输入对象(`assemble_execution(...)` 系列);装配签名不得含 `AsyncSession` 或 ORM 类型;验证:模块可被单独导入,既有服务测试全部通过且无断言修改
- [x] 1.2 `WorkflowExecutionService` 瘦身为平台 adapter:AgentModel(:391—421)、execution_mode(:884—890)、WorkflowVersion 解析(:935—942)等平台段保留在服务内,解析结果传入装配函数;公共方法签名与行为不变;验证:`tests/test_services/test_workflow/` 及 chat/evaluation/scheduling 相关回归全绿
- [x] 1.3 扩展 `tests/test_layering_domain.py`:装配模块的 import 闭包不得含 studio 与 `hecate.models`;验证:临时在装配模块加违规 import 时守卫失败,还原后通过
- [x] 1.4 核对 `runtime/AGENTS.md` 懒加载清单:新模块零新增跨域懒加载;验证:runtime 自足性探针(`tests/test_runtime/test_runtime_self_sufficiency.py`)通过

## 2. HecateExecutionBackend(PR2 前半)

- [x] 2.1 新建 `src/hecate/execution/builtin.py`:`HecateExecutionBackend` 实现六方法,经共享装配执行(D3:解析 `backend_config_ns["builtin"]`、事件循环调度、回执 pending 起步);模块 docstring 声明单事件循环假设与非持久限制;验证:模块导入纯净(不 import studio/ORM,契约纯度测试模式复用)
- [x] 2.2 实现提交幂等与状态如实性:同键同内容返回原回执、异内容 `version_conflict`、契约版本窗口检查;`get_run` 对调度异常/结果不可观察返回 `unknown`,不伪报;`request_cancel` 恒 `requested`、可选能力全部声明 `unsupported` 并显式拒绝;验证:新增单元测试覆盖五个负例场景(同键异内容、异常后 unknown、取消不 applied、unsupported 拒绝、版本窗口)
- [x] 2.3 `read_events` 事件映射:引擎事件→契约 `EventEnvelope`(event_id/source/source_sequence 必填,原始事件入 detail_ns),序号游标 + has_more;`list_artifacts` 首版返回空集并在能力声明标注;验证:事件页断言(游标续读、缺口显式)通过
- [x] 2.4 契约测试参数化复用:`tests/test_execution/test_backend_contract.py` 以同一断言集跑 Stub 与 Builtin 双实现;验证:双实现全绿,样本 schema 校验通过

## 3. 兼容样本与文档(PR2 后半)

- [x] 3.1 新增 `tests/test_execution/samples/builtin_*` 标准样本(回执、状态迁移、事件页、取消回执),断言契约结构不依赖模型文本;验证:样本与实现不一致时测试失败的漂移钉住生效(临时改字段名验证后还原)
- [x] 3.2 能力声明如实:`describe_capabilities` 带 verification 来源标注("in-process, non-durable, 0.x draft";harness=hecate,environment/cancel 语义如实);验证:与 stub 声明结构一致且字段可被契约层校验

## 4. 验证与收尾

- [x] 4.1 本地四项验证:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/test_execution/ tests/test_services/test_workflow/ tests/test_runtime/test_runtime_self_sufficiency.py tests/test_layering_domain.py -q`;验证:全部 0 错误
- [x] 4.2 `openspec validate runtime-shared-assembly --strict` 通过;场景包回归 `pytest tests/scenarios/ -q` 全绿(S01—S11 即行为兼容证据)
- [x] 4.3 方案文档 step5 操作清单的 5a 条目追加完成括注(指向本 change 与装配模块),不勾选整步;独立消费基线 §6 差距表 step5a 行状态更新;验证:git diff 仅这两处文档变更
