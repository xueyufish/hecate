# Tasks

任务组 1—2 对应 PR1(契约与模型),组 3—4 对应 PR2(登记服务与边界);组 5 收尾。每组完成即勾选。

## 1. 契约类型与数据模型

- [x] 1.1 在 `contracts/execution/references.py` 增加 `TaskRef`/`RunRef`(issuer_domain + id,`to_dict`/`from_dict`,校验非空与格式),并扩展 `tests/test_execution/test_contract_purity.py` 覆盖新类型;验证:契约纯度测试通过(不导入 SQLAlchemy/FastAPI/ORM)
- [x] 1.2 新增 `models/task.py`(`TaskModel`,无状态列)、`models/run.py`(`RunModel`:task/deployment FK、attempt_no、identity_chain/backend_ref/backend_session JSON、event_cursor、projection、origin、local_source、workspace_id)、`models/standalone_enrollment.py`、`models/conversation_link.py`,并在 `models/__init__.py` 导出;验证:模型往返测试(IdentityChain/BackendRef 序列化保真)通过
- [x] 1.3 新增 alembic 迁移建四张新表(唯一约束:`(task_id, attempt_no)`;backend 签发域+会话非空唯一的部分索引,沿用 Change 1 写法);验证:upgrade/downgrade 干净执行,建表约束与模型一致
- [x] 1.4 模型级测试:跨 workspace 隔离拒绝且不泄露存在性、`(task_id, attempt_no)` 并发唯一兜底;验证:pytest 定向通过

## 2. Task/Run 登记服务

- [x] 2.1 实现 `execution/task_run_registry.py`:`create_task`(goal/发起者身份链引用/acceptance/responsibility/workspace 校验)、`create_run`(尝试号 = max+1,固化身份链快照,绑定 Deployment 与 BackendRef);验证:重试产生新 Run 且旧 run_id 无复用路径的测试通过
- [x] 2.2 实现 `bind_backend_session`:同签发域+会话的二次绑定拒绝并报告冲突;验证:冲突负例与部分唯一索引兜底测试通过
- [x] 2.3 实现 `import_observation_run`(origin=imported_observation,断言不创建任何其他行)与 `register_standalone_enrollment`(信任根/宿主身份不可解析即拒绝;受管选择位默认 false;登记带 operator/审计字段);验证:观察导入零副作用断言与信任根拒绝测试通过
- [x] 2.4 实现 `link_conversation` 惰性映射:首次触及创建 conversation_task_links,缺治理数据时 governance_status=pending;验证:存量会话映射不改执行行为、待治理登记测试通过

## 3. 边界与分层

- [x] 3.1 扩展 `tests/test_layering_domain.py`:四张新表登记为 execution 域拥有,跨域直接写违规即失败;验证:分层测试通过,并临时构造跨域写用例确认失败后还原
- [x] 3.2 服务层负例:execution 域外无 Task/Run/映射写路径(与 3.1 互证);验证:负例测试通过

## 4. 收尾验证

- [x] 4.1 全量本地门:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/test_execution/ tests/test_layering_domain.py -q` 及受影响场景包;验证:0 错误
- [x] 4.2 `openspec validate task-run-model --strict` 通过;验证:命令输出无 error
- [x] 4.3 勾选方案 step4 第 12—17 项并附证据指针(模型/服务/测试位置;第 17 项注明"全为新表、约束即建即紧、回填为空集+惰性映射");验证:`git diff` 仅涉及这些行及其括注
