## 1. Schema 与迁移（6.47）

- [x] 1.1 `models/model_provider.py`：`ModelRegistryModel` 新增 `is_published`（bool，default False）与 `last_test_passed_at`（datetime，nullable）；`ModelRegistryReadSchema` 与管理面响应补充对应字段
- [x] 1.2 Alembic 迁移：加两列（server_default）+ 回填 `UPDATE model_registry SET is_published = true WHERE deleted = false`；编写迁移测试断言"升级前后 `/v1/models` 返回集合一致"（D6 兼容契约）
- [x] 1.3 迁移 downgrade 路径：drop 两列，验证回滚后行为回到全量引用面

## 2. 发布/取消发布端点与测试证据（6.47）

- [x] 2.1 `enterprise/api/model_providers.py` 新增 `POST /models/{model_id}/publish`：门禁校验 `last_test_passed_at` 非空，未通过返回 409 + 原因；成功置 `is_published=true` 并写审计事件（动作/目标模型/操作者/时间，复用 audit 体系）
- [x] 2.2 新增 `POST /models/{model_id}/unpublish`：无条件置 `is_published=false`，写审计事件
- [x] 2.3 `POST /models/test` 成功路径：按请求解析到的 registry 行更新 `last_test_passed_at`；解析不到 registry 行（动态发现模型）时不落证据
- [x] 2.4 单测：门禁拒绝（无证据）、发布生效、unpublish 可逆、测试成功刷新证据

## 3. 引用面门禁（6.47）

- [x] 3.1 `channel/api/v1/models.py`：registry 查询路径加 `is_published=true` 过滤；LiteLLM fallback 发现路径保持原样
- [x] 3.2 单测：未发布模型对 `/v1/models` 隐藏、对管理面列表与内联测试开放；存量 `llm_config` 绑定在模型取消发布后继续可执行（runtime 不校验，构造执行路径用例验证）

## 4. 删除护栏（6.47）

- [x] 4.1 `DELETE /models/{model_id}`（如当前为物理删除则确认现有删除路径）前置 in-use 检查：Agent `llm_config->>'model'` 与 workflow 模型配置的精确匹配扫描，命中返回 409 + 引用方清单（type/id/name）；unpublish 不受此检查限制
- [x] 4.2 单测：有引用拒绝并列出引用方、无引用可删、软删行不计入引用

## 5. 管理面查询增强（6.48）

- [x] 5.1 `GET /model-providers` 与 `GET /models` 增加 `search` 参数（model_id/display_name/provider 名称含匹配）与 `publish_state` 过滤参数（all/published/unpublished）
- [x] 5.2 调用数聚合：单条 `GROUP BY metadata->>'model'` 查询（30 天窗口 + 全量两个口径，`type='generation'`），Python 侧经 `model_registry` 映射 provider；响应附 `call_count_30d`/`call_count_total` 与 `unmatched` 桶；禁止 N+1
- [x] 5.3 单测：过滤与搜索正确性、聚合映射正确、孤儿模型入 unmatched 桶、聚合为单查询（断言查询次数）

## 6. Agent 提交警告（6.47 × 1.3.20 配对）

- [x] 6.1 `studio/agents/versioning.py` commit：快照 pin 的模型未发布时，提交照常成功，响应 warnings 附"模型 X 未发布"（走现有 warning 通道，不抛异常）
- [x] 6.2 单测：提交未发布模型成功 + 有 warning；提交已发布模型无 warning

## 7. 前端 settings/models（6.47 + 6.48）

- [x] 7.1 模型行三态徽章（已发布/调测通过/未发布，派生函数实现）+ 发布/取消发布按钮（发布按钮在无测试证据时禁用并提示原因，409 时展示后端原因）
- [x] 7.2 模型列表发布状态过滤 + provider/模型两个列表的搜索输入框（防抖接 `search` 参数）
- [x] 7.3 provider 卡片展示调用次数（30 天 + 累计）与「未匹配」桶提示；删除确认弹窗文案区分"有引用将被拒绝"
- [x] 7.4 前端组件测试：徽章派生、按钮禁用态、过滤与搜索交互

## 8. 收尾验证

- [x] 8.1 全量校验：`ruff check`、`ruff format --check`、`mypy src/`、`python -m pytest tests/ -q` 0 错误
- [ ] 8.2 手工验收：新建模型 → 测试 → 发布 → `/v1/models` 可见 → unpublish → 引用面消失且存量 agent 对话不受影响；带引用删除被拒
- [ ] 8.3 聚合耗时观察点记录（design Risks：决定是否需要表达式索引，结论写入 PR 描述）
