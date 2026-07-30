## 1. 数据模型

- [x] 1.1 在 `models/prompt.py` 中创建 `PromptModel` ORM——字段：id, workspace_id, name, created_at, updated_at, deleted_at
- [x] 1.2 在 `models/prompt.py` 中创建 `PromptVersionModel` ORM——字段：id, prompt_id, version, template(TEXT), variables(JSONB), labels(JSONB), created_at
- [x] 1.3 创建 Pydantic schema：PromptCreateSchema, PromptUpdateSchema, PromptReadSchema, PromptVersionReadSchema
- [x] 1.4 为 prompts 和 prompt_versions 表生成 Alembic 迁移
- [x] 1.5 更新 `alembic/env.py` 导入 prompt 模型

## 2. 服务层

- [x] 2.1 创建 `services/prompt_service.py` 及 PromptService 类
- [x] 2.2 实现 `create_prompt(name, template, variables)`——验证模板，创建 PromptModel + PromptVersionModel(v1)
- [x] 2.3 实现 `get_prompt(prompt_id)`——返回带有当前版本的 prompt
- [x] 2.4 实现 `update_prompt(prompt_id, template?, labels?)`——更新并创建新版本
- [x] 2.5 实现 `delete_prompt(prompt_id)`——软删除
- [x] 2.6 实现 `list_prompts(workspace_id, page, page_size)`——分页列表
- [x] 2.7 实现 `list_versions(prompt_id)`——按版本号排序的所有版本
- [x] 2.8 实现 `rollback_to_version(prompt_id, target_version)`——使用目标的模板创建新版本
- [x] 2.9 实现 `get_by_label(label)`——按部署标签获取 prompt

## 3. 模板引擎

- [x] 3.1 创建 `services/template_engine.py` 及 TemplateEngine 类
- [x] 3.2 实现 `render(template, variables)`——Jinja2 SandboxedEnvironment 渲染
- [x] 3.3 实现 `validate(template)`——验证 Jinja2 语法
- [x] 3.4 实现 `extract_variables(template)`——从模板中提取变量名

## 4. API 层

- [x] 4.1 创建 `api/management/prompts.py` 及 CRUD 端点
- [x] 4.2 实现 POST/GET/PUT/DELETE /api/prompts
- [x] 4.3 实现 GET /api/prompts/{id}/versions 和 POST rollback
- [x] 4.4 实现 GET /api/prompts/by-label/{label}
- [x] 4.5 在主 FastAPI 应用中注册 prompt 路由

## 5. 测试

- [x] 5.1 PromptService 单元测试——CRUD, versions, rollback
- [x] 5.2 TemplateEngine 单元测试——render, validate, extract
- [x] 5.3 API 端点集成测试
