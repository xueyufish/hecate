# Tasks

## 1. 契约 schema 与映射

- [x] 1.1 新增 `src/hecate/contracts/schemas/durable-task-state.schema.json`(TaskLifecycleState 封闭枚举、终态吸收、迁移约束),验证 `test_samples_valid_against_schemas.py` 新样本通过
- [x] 1.2 新增 `durable-command-record.schema.json`(command_id、ControlCommandKind、五态回执、期望 revision)与 `durable-action-ledger.schema.json`(四态、意图对象、领取/回执/恢复对象),验证样本三方互检通过
- [x] 1.3 `event-envelope.schema.json` 增加可选 `actor`/`source` 字段并更新既有样本,验证 envelope 既有测试全量回归通过
- [x] 1.4 新建 `src/hecate/contracts/execution/durable.py` 映射(TaskLifecycleState、ControlCommandKind、CommandRecord、IdempotencyKey、ActionIntent/Claim/Receipt、四态枚举)与 `events.py` 的 actor/source 字段,验证往返序列化样本测试通过
- [x] 1.5 `execution/backend.py` 的 `CancelRequestState` 增补 `EXPIRED`,验证 step3 backend 契约测试回归通过

## 2. 接缝与 Stub

- [x] 2.1 新建 `src/hecate/execution/durable.py`:`DurableTaskStore`、`ControlCommandRecorder`、`ActionLedger` 三个 abc.ABC 接缝,出入参全为契约 dataclass,验证 mypy 通过且签名无 ORM/SQL 对象
- [x] 2.2 新建 `src/hecate/execution/stub_durable.py` InMemory 第二实现(含幂等键关联表、命令五态迁移、原子领取、四态恢复查询),验证 mypy 通过

## 3. 样本与契约测试

- [x] 3.1 新增 `tests/test_execution/samples/durable/` 标准样本(状态迁移、命令回执、幂等正负例、治理事件正负例、四态恢复),验证样本通过 schema 校验且 Python 映射可全部解析
- [x] 3.2 新增 `tests/test_execution/test_durable_contract.py` 参数化契约测试(对 InMemory Stub 断言六条 spec 要求:未知状态拒绝、终态不回退、协议成功≠applied、同键同体幂等/同键异体冲突、缺 actor 治理事件拒绝、并发领取至多一个、摘要冲突拒绝、待对账不冒充成功),验证全部通过
- [x] 3.3 在 conftest 契约测试参数集留生产实现注册位并加注释说明两轨接入方式,验证参数化收集数符合预期
- [x] 3.4 新增漂移钉子测试:契约四态字符串、副作用类别五值与 `tool-recovery` Runtime 层取值一致,单侧变更即失败;验证测试通过且人为改动一侧时确实失败
- [x] 3.5 将 `contracts/execution/durable.py`、`execution/durable.py`、`execution/stub_durable.py` 纳入 AST 纯度探针扫描范围(禁止 SQLAlchemy/FastAPI/Pregel/供应商 SDK 含懒加载),验证探针测试通过且注入负例失败

## 4. 文档与验证

- [x] 4.1 更新 `src/hecate/contracts/README.md`:durable 契约条目、治理事件 profile 说明、两轨消费指引,验证 README 与 schema 清单一致
- [x] 4.2 运行 `ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/test_execution/ -q` 全部 0 错误,验证分层测试(`test_layering_domain.py`、`test_layering_entry_imports.py`)无新增违规
