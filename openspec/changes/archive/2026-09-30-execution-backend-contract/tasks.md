# Tasks

任务组 1—2 对应 PR1(契约骨架与核心类型),组 3 对应 PR2(接口/Stub/契约测试),组 4—5 对应 PR3(manifest + Sandbox 契约),组 6 收尾。每组完成即勾选。

## 1. 契约骨架与权威 schema

- [x] 1.1 创建 `src/hecate/contracts/schemas/` 七个主题 schema 文件(execution-request、event-envelope、errors、capabilities、references、artifact-manifest、sandbox),每个带 `$id`(契约名 + 0.x 版本);验证:`jsonschema` 能加载全部文件且 `$id` 唯一
- [x] 1.2 编写标准样本 `tests/test_execution/samples/`:每类请求、事件(含缺口标记样本)、六类错误、能力声明(含三轴归属与验证来源)、引用(平台侧与供应商侧各一)、制品 manifest、sandbox 状态各至少一个;验证:样本对相应 schema 校验全部通过
- [x] 1.3 三方互检测试:样本×schema 校验、映射解析样本、映射往返语义一致;验证:临时改任一方(如给 envelope 加必填字段)时测试失败后还原

## 2. Python 映射(纯 dataclass)

- [x] 2.1 实现 `contracts/execution/` dataclass 映射:BackendRef(kind/issuer_domain/id)、ExecutionRequest、EventEnvelope、BackendError 六类错误码、BackendCapabilities(CapabilityLevel + OwnershipAxes);不用 pydantic;验证:纯 stdlib/typing 依赖,映射往返测试通过
- [x] 2.2 引用类型分离测试:TaskRef/RunRef 与 SessionRef/TurnRef 为不同 kind 枚举,构造 kind 不匹配的引用非法;验证:负例测试断言类型不混用
- [x] 2.3 编码规则文档化(未知字段容忍、时间 RFC3339、枚举字符串、可空显式)写入 schema 文件 description 与 `contracts/README.md`;验证:样本中含未知字段的用例通过且不丢失

## 3. 接口、Stub 与契约测试

- [x] 3.1 实现 `execution/backend.py`:`AgentExecutionBackend` 六方法 ABC + 能力声明类型;`execution/stub.py`:`StubExecutionBackend`(pause 永久 unsupported,事件/产物/取消语义最小确定性实现);验证:Stub 可实例化并通过参数化契约测试
- [x] 3.2 契约测试 `tests/test_execution/test_backend_contract.py`:六方法行为、unsupported 能力返回结构化 UNSUPPORTED 错误(Stub 暂停负例)、幂等键冲突返回冲突、游标续读与缺口标记、超时表达为 outcome_unknown 不标 failed;验证:全部通过
- [x] 3.3 版本协商测试:请求声明超出支持窗口的契约版本返回 version_conflict;验证:负例断言通过

## 4. 制品 manifest

- [x] 4.1 实现 manifest 校验纯函数(`contracts/` 内):tar.gz + 逐文件 sha256 校验、结构校验、命名空间字段放行、明文凭据类字段拒绝;验证:摘要篡改/结构非法/命名空间外专属图三个负例失败,合法样本通过
- [x] 4.2 manifest 样本与文档:合法制品样本 + "step5 加载器消费、step11 沿用发布语义"的边界说明;验证:样本进入三方互检

## 5. SandboxProvider 契约

- [x] 5.1 实现 `execution/sandbox.py`:SandboxProvider ABC(能力发现/创建/查询/命令/文件传输/终止/续租,环境与命令幂等 ID)+ 状态枚举(创建中/就绪/终止中/已终止/失败/状态未知)+ 测试替身;验证:替身通过契约测试
- [x] 5.2 Sandbox 契约测试:重复创建幂等、重复命令提交无第二次副作用、命令超时=unknown/待对账、终止请求与实际销毁分离、快照 unsupported 负例、版本独立于执行契约;验证:全部通过

## 6. 纯度探针与收尾

- [x] 6.1 纯度探针 `tests/test_execution/test_contract_purity.py`:AST 全量扫描 `contracts/`+`execution/` 的全部 import 位置,禁止业务域/ORM/Web/Pregel 具体类/供应商 SDK 前缀;验证:临时注入函数内 `from hecate.models...` 时失败后还原;`tests/test_layering_domain.py` 登记两个新顶层前缀
- [x] 6.2 本地四项验证:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/test_execution/ -q`;验证:全部 0 错误
- [x] 6.3 `openspec validate execution-backend-contract --strict` 通过;更新方案文档 step3 清单:仅勾选契约主体相关项并注明"非 Python 验证、接入指南与冻结归 `execution-backend-nonpython-pilot`";验证:勾选项与交付物一一对应
