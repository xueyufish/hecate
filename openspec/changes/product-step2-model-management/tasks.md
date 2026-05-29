## 1. 数据模型与迁移

- [x] 1.1 创建 ModelProviderModel ORM 模型（id, name, display_name, api_key_encrypted, base_url, config JSON, is_enabled, status, timestamps）
- [x] 1.2 创建 ModelRegistryModel ORM 模型（id, provider_id FK, model_id, display_name, model_type, capabilities JSON, max_context, is_custom, is_enabled, timestamps）
- [x] 1.3 创建 Pydantic schemas：ModelProviderCreateSchema, ModelProviderUpdateSchema, ModelProviderReadSchema, ModelRegistryReadSchema
- [x] 1.4 生成 Alembic 迁移 006_model_providers.py，创建 model_providers 和 model_registry 表
- [x] 1.5 在 conftest.py 导入新模型，确保测试数据库自动建表

## 2. API Key 加密层

- [x] 2.1 在 config.py 添加 FERNET_KEY 配置项
- [x] 2.2 创建 services/model_provider/crypto.py，实现 Fernet 加密/解密函数
- [x] 2.3 FERNET_KEY 未设置时 fallback 到明文存储
- [x] 2.4 编写加密/解密单元测试

## 3. Provider CRUD API

- [x] 3.1 创建 api/management/model_providers.py 路由文件
- [x] 3.2 POST /api/model-providers — 创建 Provider（加密 API Key，调用 LiteLLM 发现模型，写入 model_registry）
- [x] 3.3 GET /api/model-providers — 列出所有 Provider（含状态、模型数量）
- [x] 3.4 PUT /api/model-providers/{id} — 更新 Provider（API Key 变更时重新发现模型）
- [x] 3.5 DELETE /api/model-providers/{id} — 删除 Provider（级联删除 model_registry）
- [x] 3.6 POST /api/model-providers/{id}/test — 测试连通性（轻量 LLM 调用，更新状态）
- [x] 3.7 在 main.py 注册路由
- [ ] 3.8 编写 Provider CRUD + 测试连通性单元测试

## 4. 模型注册表 API

- [x] 4.1 GET /api/models — 列出所有注册模型（按 Provider 分组）
- [x] 4.2 PUT /api/models/{id} — 更新模型（启用/禁用、显示名）
- [x] 4.3 POST /api/models — 手动添加自定义模型（is_custom=true）
- [ ] 4.4 编写模型注册表 API 单元测试

## 5. 模型调试 API

- [x] 5.1 POST /api/models/test — 接收 model_id + prompt + 参数，调用 llm_service.chat()，返回响应
- [x] 5.2 参数校验：temperature 0-2，max_tokens >= 1
- [x] 5.3 错误处理：模型不可用时返回 400 + LiteLLM 错误信息
- [ ] 5.4 编写模型调试 API 单元测试

## 6. 改造 /v1/models 端点

- [x] 6.1 修改 _discover_models() 优先从 model_registry 读取（只返回 enabled + chat 类型）
- [x] 6.2 无数据库 Provider 时 fallback 到 LiteLLM get_valid_models()
- [x] 6.3 返回格式改为按 Provider 分组（provider_display_name + models）
- [ ] 6.4 编写改造后的 /v1/models 单元测试

## 7. Provider 级配置

- [x] 7.1 Provider config JSON 校验：timeout (1-300), max_retries (0-10), rate_limit_rpm (1-10000)
- [x] 7.2 默认值处理：timeout=30, max_retries=3, rate_limit_rpm=60
- [ ] 7.3 LLM 调用时应用 Provider 级 timeout/retry 配置
- [ ] 7.4 编写配置校验单元测试

## 8. Fallback 集成

- [ ] 8.1 Provider 状态变化时，查询使用该 Provider 模型的 Agent 列表
- [ ] 8.2 Agent 列表 API 返回时，附加 model_available 字段（检查 Provider 状态）
- [ ] 8.3 前端 Agent 列表显示"模型不可用"警告标记
- [ ] 8.4 编写 fallback 集成单元测试

## 9. 前端 — 设置页面

- [x] 9.1 创建 web/src/app/(dashboard)/settings/layout.tsx 设置页布局
- [x] 9.2 创建 web/src/app/(dashboard)/settings/models/page.tsx Provider 列表页（表格 + 状态徽章 + 操作按钮）
- [x] 9.3 创建 Provider 添加/编辑对话框（表单：name, display_name, api_key, base_url, config）
- [x] 9.4 创建 Provider 测试连通按钮（点击后显示状态和响应时间）
- [x] 9.5 创建模型管理子页面（按 Provider 分组的模型表格，启用/禁用开关）
- [x] 9.6 创建模型调试页面（模型下拉 + prompt 输入 + 参数滑块 + 测试按钮 + 响应区）
- [x] 9.7 侧边栏添加"设置"导航入口

## 10. 前端 — Agent 创建模型选择改造

- [x] 10.1 修改 web/src/app/(dashboard)/agents/new/page.tsx 的模型下拉组件
- [x] 10.2 改为按 Provider 分组的 `<optgroup>` 展示（provider display_name 为组名）
- [x] 10.3 处理空模型列表状态（显示引导用户配置 Provider 的提示）

## 11. 集成测试与验证

- [ ] 11.1 端到端测试：创建 Provider → 自动发现模型 → 模型出现在 /v1/models
- [ ] 11.2 端到端测试：创建 Agent 选择分组模型 → 对话成功
- [ ] 11.3 端到端测试：Provider 状态变化 → Agent 列表显示警告
- [ ] 11.4 运行 ruff check + ruff format + mypy + pytest 全量验证
