# Tasks

任务组 1—2 对应 PR1(HTTP 绑定与身份传递),组 3—4 对应 PR2/PR3(工具契约、托管映射,可按体量合并为一个 PR),组 5 收尾。每组完成即勾选。

## 1. HTTP/JSON 绑定与错误映射(PR1 前半)

- [x] 1.1 将 `openapi-spec-validator` 加入 `pyproject.toml` dev extra 并 `uv pip install --prerelease=allow -e ".[dev]"`;验证:导入成功且 `python -c "import openapi_spec_validator"` 通过
- [x] 1.2 新建 `src/hecate/contracts/openapi/execution-backend.http.v0_1.yaml`(OpenAPI 3.1):`$ref` 复用 `schemas/` 全部文件、六端点(capabilities/submit/get_run/read_events/cancel/artifacts)+ SSE 可选视图端点、`Idempotency-Key` 头、problem+json 错误响应(四可返回码映射 501/403/429/409,`type` 为契约错误 URI)、传输中立声明与"调用方合成态"(`unreachable`/`outcome_unknown` 非后端错误)专节、`contract_version` 协商与 `X-Contract-Version`;验证:`openapi-spec-validator` 结构校验通过,文件内无任何从 schema 复制的字段定义(人工核对 $ref)
- [x] 1.3 新增 HTTP 层样本 `tests/test_execution/samples/http/`:提交 202 收据、幂等冲突 409、403/501/429 错误体、事件页(cursor+next_cursor+gap marker)、取消 receipt、含 `outcome_unknown` 的 run 状态 payload;负例样本:以 `unreachable`/`outcome_unknown` 为错误码的 problem+json(必须校验失败);验证:正例按 OpenAPI media schema 校验通过,负例按 OpenAPI 校验失败
- [x] 1.4 编写 `tests/test_execution/test_http_binding.py`:OpenAPI 结构校验、样本×media schema 互检、错误映射表一致性(六码中四可返回码与状态码一一对应)、SSE 端点标记为 optional;验证:pytest 通过且临时改动映射表(如把 429 改 402)时测试失败后还原
- [x] 1.5 勾选方案 step3"为首个进程外接入规定 HTTP/JSON 绑定"项并附证据指针;验证:方案文档 diff 仅该项

## 2. 身份与授权传递(PR1 后半)

- [x] 2.1 新建 `schemas/security-claims.schema.json`(声明集:`iss`/`aud`/`sub`/`tenant`/`delegation_ref`/`exp`,短时效为规范约束非 schema 断言)+ 样本:合法声明集、缺 `aud` 负例、body 携带 `role: admin` 的自报负例;验证:样本互检通过/负例失败
- [x] 2.2 OpenAPI 增 `securitySchemes`(`bearerHttp` + `mutualTLS`)与回调认证节(后端→平台回调独立受众凭据、禁止复用入站令牌);验证:结构校验通过,回调节含受众绑定要求文本
- [x] 2.3 `contracts/execution/` 增声明集映射与测试:解析/往返/未知字段保留;映射行为测试断言 body 自报身份字段不进入任何鉴权数据结构;验证:pytest 通过
- [x] 2.4 勾选方案 step3"定义进程外服务身份与授权上下文传递"项;验证:方案文档 diff 仅该项

## 3. 工具声明契约(PR2)

- [x] 3.1 新建 `schemas/tool.schema.json`(`input_schema_ref`/`output_schema_ref`/`version`/`side_effect_class` 五值枚举,注释指向 `runtime/tool_side_effects.py` 对齐源)+ 声明样本(readonly 与 non_idempotent_write 各一);验证:样本校验通过
- [x] 3.2 四态失败分层样本与 event envelope `tool_selection` 可选扩展字段:提交前参数拒绝(problem+json 400 绑定层样本)、业务拒绝=工具结果事件(否定 payload,Run 继续)、工具错误事件、`outcome_unknown` 复用+对账;验证:四类样本各就各位且互检通过
- [x] 3.3 映射与测试:枚举一致性测试 import `SideEffectClass` 钉死五值;四态分层断言(业务拒绝不产生执行错误事件、参数拒绝在分发前、结果未知进对账);验证:pytest 通过
- [x] 3.4 勾选方案 step3"为工具契约固定输入/输出 schema、版本与副作用类别"项;验证:方案文档 diff 仅该项

## 4. 托管后端映射语义(PR3)

- [x] 4.1 新建 `src/hecate/contracts/hosted-mapping.md`:task↔session、run↔turn 映射表、状态映射指引、subagent 事件命名空间化规则(引用方案 step12 边界,绝不自动成为 Team 成员)、提交响应丢失对账流程;验证:文档与 references/capabilities schema 术语一致(无新造引用 kind)
- [x] 4.2 `errors.schema.json` 的 `reconciliation.strategy` 增 `query_by_vendor_session`,`capabilities.schema.json` 增对账能力声明字段;受影响 schema 版本 bump 0.1→0.2;样本:按会话对账正例、后端不支持该策略标未知负例;验证:样本互检与既有测试全部通过(枚举扩展向后兼容)
- [x] 4.3 勾选方案 step3"为托管后端定义平台 Task/Run 到供应商 session/turn…映射"项;验证:方案文档 diff 仅该项

## 5. 验证与收尾

- [x] 5.1 本地四项门:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/test_execution/ tests/test_layering_domain.py -q`(含纯度探针与新测试);验证:全部 0 错误
- [x] 5.2 `openspec validate execution-backend-bindings --strict` 通过;核对 `contracts/README.md` 编码规则与新文件一致(需要时补一句绑定文件指引);验证:命令无 error,README 与交付物一致
