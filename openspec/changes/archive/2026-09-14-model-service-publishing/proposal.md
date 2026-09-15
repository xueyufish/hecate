## Why

模型的"注册即可被应用引用"缺少质量关卡：任何模型只要添进 `model_registry` 就出现在 `/v1/models` 与 Create Agent 模型下拉中，无论是否调测通过。同时管理面缺少两个低成本快赢项：provider/模型列表无搜索过滤、provider 卡片不展示调用次数（数据已存在于 traces，只是未暴露）。Roadmap Sprint 8 Opening Queue 之后的 Model Management 块（6.47 + 6.48），且 6.47 的提交/发布语义与刚交付的 1.3.20 Agent Versioning 同构配对。

## What Changes

- **模型发布状态**：`model_registry` 新增 `is_published`（bool）与 `last_test_passed_at`（测试证据）。UI 派生三态徽章：未发布 / 调测通过 / 已发布；「testing」是派生态（测试已通过的证据），不是存储态。
- **发布动作**：新增 publish / unpublish 端点。发布是显式动作，门禁为模型测试已通过；unpublish 自由可逆。
- **引用面门禁**：`GET /v1/models`（应用引用面：Create Agent 模型下拉）只返回已发布模型；settings 管理面与内联测试不受限。存量 agent `llm_config` 绑定不受影响（reference-only 语义，业界共识：CubePlex/OpenRouter/AgentScope 均对新引用隐藏、存量引用继续工作）。
- **删除护栏**：删除 registry 模型前做 in-use 检查（agent `llm_config`、workflow 模型引用），有引用时拒绝并列出引用方——避免 Dify 式"删除后应用静默失败"。
- **发布留痕**：publish/unpublish 作为可审计动作记录（复用现有 audit 体系）。
- **提交警告**（1.3.20 配对）：Agent 版本提交时若 pin 的模型未发布，commit 响应附 warning（警告不阻断）。
- **6.48 快赢**：provider 与模型列表新增搜索过滤；provider 卡片展示调用次数（30 天滚动窗口 + 累计可选），单条聚合查询 `GROUP BY traces.metadata->>'model'` + registry join，孤儿模型归「未匹配」桶。
- **迁移**：存量 registry 行回填 `is_published=true`（零破坏），新注册模型默认未发布。

## Capabilities

### New Capabilities
- `model-service-publishing`: 模型发布生命周期（发布状态机、测试证据、引用面门禁、unpublish/删除护栏、发布留痕、提交警告）与模型管理面增强（列表搜索过滤、provider 调用次数统计）。

### Modified Capabilities

## Impact

- **Schema**：`model_registry` 加 2 列 + Alembic 迁移（含回填）；`models/model_provider.py` ORM 与 Read schema 更新。
- **API**：`enterprise/api/model_providers.py`（publish/unpublish 端点、delete 护栏、列表过滤参数、聚合统计）；`channel/api/v1/models.py`（published 过滤）。
- **Studio**：`studio/agents/versioning.py`（commit 时的未发布模型 warning）。
- **前端**：`web/src/app/(dashboard)/settings/models/page.tsx`（徽章、发布按钮、状态过滤、搜索框、provider 卡片调用数）。
- **不做**（范围红线）：运行时回收存量引用；多租户模型访问组（LiteLLM access-groups 式，挂 P5 6.46 治理方向）；canary 切流（已有 6.8a）；弃用阶梯 + sunset 通知（watsonx 式，P5）；模型别名层；6.45 `model_deployments` 通道机制（不同表不同概念，不复用）。
