## 1. 后端：Agent 模板文件

- [x] 1.1 创建 `src/hecate/data/agent_templates/` 目录
- [x] 1.2 创建 `customer-service.json` 模板
- [x] 1.3 创建 `code-review.json` 模板
- [x] 1.4 创建 `research-assistant.json` 模板
- [x] 1.5 创建 `content-writer.json` 模板
- [x] 1.6 创建 `data-analyst.json` 模板

## 2. 后端：Template API

- [x] 2.1 创建 `src/hecate/api/management/agent_templates.py`，包含模板加载和缓存
- [x] 2.2 实现 `GET /api/agent-templates` 端点（列表并返回元数据）
- [x] 2.3 实现 `GET /api/agent-templates/{id}` 端点（完整模板）
- [x] 2.4 实现 `POST /api/agent-templates/{id}/instantiate` 端点，带 KB ID 验证
- [x] 2.5 在主应用中注册路由

## 3. 后端：测试

- [x] 3.1 添加模板列表端点测试
- [x] 3.2 添加模板详情端点测试（成功 + 404）
- [x] 3.3 添加模板实例化测试（成功 + 无效 KB ID）

## 4. 前端：模板选择器

- [x] 4.1 创建 `web/src/components/agent/template-picker.tsx` 组件
- [x] 4.2 按类别分组显示模板，带预览卡片
- [x] 4.3 在 Agent 创建页面添加"From Template"按钮
- [x] 4.4 选择模板后预填充表单

## 5. 验证

- [x] 5.1 运行 `ruff check src/hecate/ tests/` — 零错误
- [x] 5.2 运行 `ruff format --check src/ tests/` — 零错误
- [x] 5.3 运行 `mypy src/` — 零错误
- [x] 5.4 运行 `python -m pytest tests/ -q` — 所有测试通过
- [x] 5.5 在 `web/` 目录运行 `npm run lint` 和 `npm run build` — 零错误
