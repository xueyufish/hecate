## Context

现状（代码事实）：

- `ModelRegistryModel`（`src/hecate/models/model_provider.py`）只有 `is_enabled`，无发布状态；`model_providers.status` 已有 provider 级测试状态语义（"inactive/active"，前端显示"待测试"徽章）。
- 管理面 API 在 `enterprise/api/model_providers.py`：`GET /model-providers`、`GET /models`、`PUT /models/{model_id}`、`POST /models`（自定义模型）、`POST /models/test`（无状态试跑，不留痕）、`POST /model-providers/{provider_id}/test`（连通性测试，结果写 provider status）。
- 应用引用面 `GET /v1/models`（`channel/api/v1/models.py`）当前返回全部启用（`is_enabled=true`）的 chat 模型；无 provider 时 fallback 到 LLM gateway 动态发现（该路径无 registry 行，无发布概念）。
- Agent 以 `llm_config.model`（字符串 model_id）引用模型，1.3.20 已明确"模型权重可在同一模型 ID 后更新"——模型身份是引用而非版本，本次门禁沿用该哲学。
- 调用统计的数据源：traces 表无 provider/model 专属列，模型标识在 `traces.metadata->>'model'`（`ops/cost.py` 成本聚合已有同款取数先例）。
- 调研结论（2026-09-14，五组子代理）：业界可见性门禁一致为 reference-only（CubePlex/OpenRouter/AgentScope/Dify 均对新引用隐藏、存量引用继续工作）；"testing" 从不作为用户可停留的存储态（Palantir/Bedrock/CatPaw 的发布均为显式动作）。

## Goals / Non-Goals

**Goals:**

- 最小 schema 变更实现完整发布生命周期（列 + 门禁 + 过滤 + 护栏 + 留痕）。
- 升级零破坏：迁移回填保证 `/v1/models` 集合不变。
- 管理面三态可视化 + 6.48 两个快赢项（搜索、调用数）。
- 发布门禁的证据链可解释（按钮可用条件 = 有测试通过证据）。

**Non-Goals:**

- 不复用 6.45 `model_deployments`（dev/staging/prod 通道机制，面向已部署模型版本；与 registry 条目的发布状态是不同表、不同概念——catalog"复用 6.45 机制"的说法按此修正，实际复用的是测试端点作门禁前置 + 1.3.20 的提交/发布语义范式）。
- 不做运行时回收存量绑定、不做多租户模型访问组（正交轴，P5 6.46 方向）、不做 canary 切流（6.8a 已有）、不做弃用阶梯/sunset 通知、不做别名层。
- 不改 LiteLLM fallback 发现路径（无 registry 行，无发布概念，保持原样）。

## Decisions

### D1: 存储发布标志 + 派生 testing 态，而非全存储三态

`model_registry` 新增两列：`is_published`（bool，默认 false）、`last_test_passed_at`（datetime，nullable）。UI 三态徽章为派生：已发布 / 调测通过（`last_test_passed_at` 非空且未发布）/ 未发布。

- 备选：存储 `publish_state` 字符串枚举（draft/testing/published）。否决理由："testing" 不是模型停留的状态而是"测试已通过"的证据，存储态需要额外的进入/退出触发时机（何时不一致于测试结果？），且与现有 `POST /models/test` 无状态试跑的形态冲突。bool 命名与表内 `is_enabled` 惯例一致。
- `retired` 终态：v1 不引入；P5 6.46 治理如需弃用阶梯，届时再迁移（bool → 枚举为廉价迁移）。

### D2: 门禁为 reference-only；agent 提交警告不阻断

`GET /v1/models` 加 `is_published=true` 过滤。存量 `llm_config` 绑定不做运行时校验/回收。1.3.20 提交路径（`studio/agents/versioning.py` commit）在快照 pin 的模型未发布时于响应附 warning（复用 drift/warning 通道，不抛异常）。

- 依据：八家有门禁的平台无一阻断存量引用；阻断会把已交付的两个面（1.3.20 × 6.47）强耦合。发布门禁（测试通过）已提供质量信号。

### D3: unpublish 自由；delete 带引用护栏

区分两个动作：取消发布仅翻转 `is_published`，无条件限制（可逆、自愈）；删除模型前执行 in-use 检查——扫描 Agent `llm_config` 与 workflow 模型配置对该 `model_id` 的精确引用，命中则 409 返回引用方清单（`{type, id, name}`）。删除 provider 沿用现有 CASCADE 语义，不在本次加护栏（provider 删除已隐含删除其模型行，风险提示由前端确认弹窗承担）。

- 备选：删除时自动迁移引用到默认模型。否决：隐式迁移违反最小意外原则，v1 拒绝 + 清单即可。
- in-use 扫描口径：Agent 按 `llm_config->>'model' == model_id` 精确匹配；workflow 按 DSL 中模型引用字段匹配。JSON LIKE 扫描在当前数据量（管理面触发、单租户操作）下可接受，不做全文预索引。

### D4: 调用数聚合为单条 GROUP BY + registry join，30 天滚动 + 累计双口径

`GET /model-providers` 响应附 `call_count_30d` 与 `call_count_total`。实现为一条聚合查询：`SELECT metadata->>'model' AS model, COUNT(*) FROM traces WHERE type='generation' AND start_time >= now()-30d GROUP BY 1`（累计口径同理去掉时间条件），Python 侧经 `model_registry`（model_id → provider_id）映射进 provider 列表；未命中 registry 的 model 标识聚合为 `unmatched` 桶随响应返回，前端展示「未匹配」。

- 备选：SQL 侧 JOIN JSON 提取、或物化预聚合表。否决：当前规模下单条查询足够（管理面页面触发，非高频热路径）；预聚合留给量级出现后的演进（见 Risks）。
- 不做 N+1：禁止逐 provider 循环查询。

### D5: 发布/取消发布端点与留痕

新增 `POST /models/{model_id}/publish`、`POST /models/{model_id}/unpublish`（`enterprise/api/model_providers.py`）。publish 门禁：`last_test_passed_at` 非空，否则 409 返回原因。两个动作写审计事件（复用现有 audit 体系，`models/audit.py`），含动作类型、目标模型、操作者、时间。

- 测试证据写入：`POST /models/test` 成功后按请求的 model_id 更新对应 registry 行的 `last_test_passed_at`（一行测试可能对应多 provider 同名模型——按请求实际解析到的 registry 行更新；解析不到的动态模型不落证据）。

### D6: 迁移回填方向为 published

Alembic 迁移：加两列 + `UPDATE model_registry SET is_published = true WHERE deleted = false`（含已软删行不回填，语义无关）。兼容契约：升级前后 `/v1/models` 集合不变；新注册模型走默认 false。

## Risks / Trade-offs

- [traces JSON 聚合在大表上变慢] → 30 天窗口 + `type='generation'` 过滤先行；上线后观察耗时，超阈值再加表达式索引 `((metadata->>'model'), start_time)` 或转预聚合表（任务清单留观察点，不预建）。
- [in-use 精确匹配漏检间接引用（如 workflow 模板变量间接指定模型）] → 引用清单以精确匹配为承诺口径，护栏文档明示；漏检后果是删除成功 + 引用方运行时报错（与 Dify 现状相同，不劣于基线）。
- [回填方向写反导致引用面清空] → D6 固化为迁移验收断言（迁移测试对比升级前后 `/v1/models` 集合）。
- [publish 后模型质量问题暴露给全部应用] → v1 接受：门禁已含测试证据；租户级灰度/访问组属 P5 范围。
- [三态徽章与未来 retired 态冲突] → 前端徽章按派生函数实现，新增状态只改一处。

## Migration Plan

1. Alembic 迁移：`model_registry` 加 `is_published`（server_default=false）与 `last_test_passed_at`，回填存量非删除行为 true。
2. 后端部署（过滤 + 端点 + 护栏）与迁移同批生效；回滚 = 迁移 downgrade（drop 两列，行为回到全量引用面）。
3. 前端独立部署，无 schema 依赖（后端未上前端字段缺省即可）。

## Open Questions

- traces 聚合是否需要表达式索引：待上线后按实际耗时决定（已留观察任务，不阻塞实现）。
- traces 记录 declared vs actual model（OpenRouter 式）：本版本不做；`metadata.model` 口径已够 6.48 聚合，未来引入路由层时再补。
