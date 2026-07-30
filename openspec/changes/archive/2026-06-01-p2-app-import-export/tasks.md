## 1. Backend: Export Endpoint — 后端：导出端点

- [x] 1.1 在 agents.py 中添加 `GET /api/agents/{id}/export` 端点
- [x] 1.2 构建包含 version、exported_at、agent 配置的导出 JSON
- [x] 1.3 如果代理具有 mode=workflow，则包含工作流 Graph DSL
- [x] 1.4 包含代理的内存块
- [x] 1.5 设置 Content-Disposition 头以支持文件下载

## 2. Backend: Import Endpoint — 后端：导入端点

- [x] 2.1 在 agents.py 中添加 `POST /api/agents/import` 端点
- [x] 2.2 验证 JSON 格式和必填字段
- [x] 2.3 从导出的配置创建新代理
- [x] 2.4 如果导出中包含工作流，则创建工作流
- [x] 2.5 如果导出中包含内存块，则创建内存块
- [x] 2.6 优雅地处理缺失的 KB（记录警告、跳过）

## 3. Backend: Tests — 后端：测试

- [x] 3.1 添加导出端点测试（成功、包含工作流、包含内存块、404）
- [x] 3.2 添加导入端点测试（成功、包含工作流、包含内存块、无效 JSON）
- [x] 3.3 添加导入时缺少 KB 的测试

## 4. Frontend: Export Button — 前端：导出按钮

- [x] 4.1 在代理详情页面添加"导出"按钮
- [x] 4.2 下载名为 `{agent-name}.json` 的 JSON 文件

## 5. Frontend: Import Button — 前端：导入按钮

- [x] 5.1 在代理列表页面添加"导入代理"按钮
- [x] 5.2 打开 JSON 文件上传对话框
- [x] 5.3 上传文件并在成功后导航到新代理
- [x] 5.4 导入失败时显示错误消息

## 6. Verification — 验证

- [x] 6.1 运行 `ruff check src/hecate/ tests/` — 零错误
- [x] 6.2 运行 `ruff format --check src/ tests/` — 零错误
- [x] 6.3 运行 `mypy src/` — 零错误
- [x] 6.4 运行 `python -m pytest tests/ -q` — 所有测试通过
- [x] 6.5 在 `web/` 中运行 `npm run lint` 和 `npm run build` — 零错误
