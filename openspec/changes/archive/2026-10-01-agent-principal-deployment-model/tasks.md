# Tasks

任务组 1—3 分别对应 PR1(类型与 schema)、PR2(回填与服务层)、PR3(负例与收尾);每组完成即勾选。

## 1. 类型与 schema(PR1)

- [x] 1.1 在 `contracts/execution/` 新增 `IdentityChain` frozen dataclass(发起者/agent principal 引用/工作负载身份/on-behalf-of 委派),校验非空引用,序列化用 `BackendRef.to_dict` 约定;验证:`tests/test_execution/` 新增类型测试通过且 `test_contract_purity.py` 仍绿(contracts 不引入 ORM 依赖)
- [x] 1.2 新增 `models/agent_principal.py`(AgentPrincipalModel:agent 关联、组织/负责人外键、生命周期枚举、IdP 映射 JSON、审计字段)与 `models/agent_deployment.py`(AgentDeploymentModel:agent_version/agent 外键、backend_type+版本、接入方式、传输契约版本、endpoint/配置引用、能力快照 JSON、axes 三枚举列、issuer_domain、默认标记、凭据引用、hosted_config JSON);`models/__init__.py` 导出;验证:import 成功且命名符合 XxxModel 惯例
- [x] 1.3 alembic expand revision 建两张表(宽松约束 + agent_id+backend_type 唯一索引);验证:`alembic upgrade head` 于测试库通过,`alembic downgrade` 一级可回退
- [x] 1.4 分层规则:扩展 `tests/test_layering_domain.py`,禁止 execution 之外的域 import 两张新表 Model 类;验证:构造违规 import 的临时用例失败后还原,既有分层测试全绿

## 2. 回填与服务层(PR2)

- [x] 2.1 `src/hecate/execution/principal_registry.py`:principal 登记/生命周期转换/撤销后拒绝新 Deployment 引用,负责人必须解析到组织+用户登记,待治理项写 audit;验证:principal 单元测试覆盖 spec 三场景(owner 链接/persona 非负责人/撤销拒绝)
- [x] 2.2 `src/hecate/execution/deployment_registry.py`:唯一写入方——登记(同版本多后端并存、默认标记至多一个、签发域唯一)、`to_backend_ref()`/`to_axes()` 契约转换、hosted_config 校验(未核验哨兵、坏 JSON 拒绝、脏 axes 拒绝)、登记审计记录;验证:registry 测试覆盖 spec 场景(双后端并存/BackendRef 解析/语言元数据不参与判定/未核验驻留显式/axes 双向转换)
- [x] 2.3 alembic migrate+contract revision:确定性 ID 回填 builtin Deployment(幂等,重放不产生第二条)、设默认标记、收紧 NOT NULL、存量 Agent 待治理项写 audit(不建 principal);验证:迁移前后执行链路回归测试(chat/workflow 现有测试)全绿 + 迁移重放幂等测试
- [x] 2.4 workspace 隔离:registry 查询必经 Agent join 校验,跨 workspace 统一 NotFound 语义;验证:跨 workspace 读/写/指定默认部署的负例测试通过且响应不泄露目标存在性

## 3. 负例与收尾(PR3)

- [x] 3.1 迁移三段演练:expand→migrate→contract 全程 + 保留新表的应用层回滚场景(旧代码路径忽略新表);验证:演练脚本/测试证明回滚不丢运行记录
- [x] 3.2 边界负例:非 execution 域直接写表被分层测试拦截;同一 agent_id+backend_type 重复登记被唯一索引拒绝; revoked principal 引用被拒绝;验证:四类负例测试全部通过
- [x] 3.3 本地四项验证:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/test_execution/ tests/test_models/ -q`(及受影响的既有目录);验证:0 错误
- [x] 3.4 `openspec validate agent-principal-deployment-model --strict` 通过;方案 step4 前五项勾选并附证据指针(1—5 项:定义保留/principal/身份区分/Deployment 表/托管双轴);验证:diff 仅涉及对应行
