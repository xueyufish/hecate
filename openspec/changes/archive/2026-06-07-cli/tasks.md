## 1. 设置 — 依赖和入口点

- [x] 1.1 将 `typer>=0.15.0` 和 `rich>=13.0.0` 添加到 pyproject.toml 的主要依赖中
- [x] 1.2 添加 `[project.scripts]` 入口点：`hecate = "hecate.cli.main:app"`
- [x] 1.3 安装新依赖：`uv pip install -e ".[dev]"`
- [x] 1.4 创建 `src/hecate/cli/__init__.py`（空）
- [x] 1.5 创建 `src/hecate/cli/commands/__init__.py`（空）

## 2. 核心基础设施

- [x] 2.1 实现 `src/hecate/cli/config.py` — TOML 配置加载器、配置文件管理、`get_config()`、`set_config()`、`get_active_profile()`、首次运行时创建配置文件
- [x] 2.2 实现 `src/hecate/cli/client.py` — HecateClient 类，包含 httpx 同步客户端、auth 标头注入、错误处理、连接检查
- [x] 2.3 实现 `src/hecate/cli/output.py` — 使用 rich Table 的 `format_table()`、用于 --json 输出的 `format_json()`、用于 API 错误的 `display_error()`、用于破坏性操作的 `confirm_delete()`
- [x] 2.4 实现 `src/hecate/cli/main.py` — 根 typer app、`--profile` 选项、`--json` 全局标志、`--version` 标志、注册所有子命令组

## 3. 配置和认证命令

- [x] 3.1 实现 `hecate config set <key> <value>` — 写入 config.toml 中的活动配置文件
- [x] 3.2 实现 `hecate config get <key>` — 显示单个配置值
- [x] 3.3 实现 `hecate config show` — 显示所有配置值，api_key 被屏蔽
- [x] 3.4 实现 `hecate auth login --email <email>` — 调用 POST /api/auth/login，将 token 存储到配置文件
- [x] 3.5 实现 `hecate auth whoami` — 调用 GET /api/auth/me，显示用户信息
- [x] 3.6 在 client.py 中实现 JWT 自动刷新 — 检查 token 过期时间，必要时调用 POST /api/auth/refresh

## 4. Agent 命令

- [x] 4.1 实现 `hecate agent list` — GET /api/agents，表格输出包含 id/name/mode/model
- [x] 4.2 实现 `hecate agent create` — POST /api/agents，带 --name、--model、--mode、--persona、--tools、--kb-ids
- [x] 4.3 实现 `hecate agent get <id>` — GET /api/agents/{id}，详细显示
- [x] 4.4 实现 `hecate agent update <id>` — PUT /api/agents/{id}，带可选的 --name、--persona、--tools、--kb-ids
- [x] 4.5 实现 `hecate agent delete <id>` — DELETE /api/agents/{id}，带确认提示

## 5. Session 命令

- [x] 5.1 实现 `hecate session create --agent-id <id>` — POST /api/sessions
- [x] 5.2 实现 `hecate session list` — GET /api/sessions，表格输出
- [x] 5.3 实现 `hecate session get <id>` — GET /api/sessions/{id}
- [x] 5.4 实现 `hecate session resume <id> --message <msg>` — POST /api/sessions/{id}/resume

## 6. 聊天命令（核心体验）

- [x] 6.1 实现 `hecate chat send <agent_id> <message>` — POST /v1/chat/completions（非流式），显示响应
- [x] 6.2 在 client.py 中实现 SSE 流式解析器 — 解析 `data: {...}` 行，提取 delta 内容
- [x] 6.3 实现 `hecate chat interactive <agent_id>` — REPL 循环，带流式、斜杠命令（/clear、/exit、/history）
- [x] 6.4 实现交互式聊天上下文管理 — 跨轮次维护 conversation_id，支持 --session-id 用于恢复

## 7. 知识库命令

- [x] 7.1 实现 `hecate kb list` — GET /api/knowledge-bases，表格输出
- [x] 7.2 实现 `hecate kb create` — POST /api/knowledge-bases，带 --name、--description、--embedding-model、--chunk-strategy
- [x] 7.3 实现 `hecate kb upload <kb_id> <file>` — POST /api/knowledge-bases/{id}/documents，带 multipart 上传
- [x] 7.4 实现 `hecate kb documents <kb_id>` — GET /api/knowledge-bases/{id}/documents，表格包含解析状态

## 8. 工具命令

- [x] 8.1 实现 `hecate tool list` — GET /api/tools，带可选的 --source 过滤器
- [x] 8.2 实现 `hecate tool get <id>` — GET /api/tools/{id}

## 9. 技能命令

- [x] 9.1 实现 `hecate skill list` — GET /api/skills，表格输出
- [x] 9.2 实现 `hecate skill create` — POST /api/skills，带 --name、--content、--source
- [x] 9.3 实现 `hecate skill get <id>` — GET /api/skills/{id}
- [x] 9.4 实现 `hecate skill update <id>` — PUT /api/skills/{id}
- [x] 9.5 实现 `hecate skill delete <id>` — DELETE /api/skills/{id}，带确认
- [x] 9.6 实现 `hecate skill import <file>` — POST /api/skills/import，带文件上传

## 10. 工作流命令

- [x] 10.1 实现 `hecate workflow list` — GET /api/workflows，表格输出
- [x] 10.2 实现 `hecate workflow create` — POST /api/workflows，带 --name、--graph-dsl（JSON 字符串或文件路径）
- [x] 10.3 实现 `hecate workflow get <id>` — GET /api/workflows/{id}
- [x] 10.4 实现 `hecate workflow update <id>` — PUT /api/workflows/{id}
- [x] 10.5 实现 `hecate workflow delete <id>` — DELETE /api/workflows/{id}，带确认
- [x] 10.6 实现 `hecate workflow validate <id>` — POST /api/workflows/{id}/validate
- [x] 10.7 实现 `hecate workflow test-run <id>` — POST /api/workflows/{id}/test-run
- [x] 10.8 实现 `hecate workflow versions <id>` — GET /api/workflows/{id}/versions
- [x] 10.9 实现 `hecate workflow runs <id>` — GET /api/workflows/{id}/runs

## 11. Prompt 命令

- [x] 11.1 实现 `hecate prompt list` — GET /api/prompts，表格输出
- [x] 11.2 实现 `hecate prompt create` — POST /api/prompts，带 --name、--content、--label
- [x] 11.3 实现 `hecate prompt get <id>` — GET /api/prompts/{id}
- [x] 11.4 实现 `hecate prompt update <id>` — PUT /api/prompts/{id}
- [x] 11.5 实现 `hecate prompt delete <id>` — DELETE /api/prompts/{id}，带确认
- [x] 11.6 实现 `hecate prompt versions <id>` — GET /api/prompts/{id}/versions
- [x] 11.7 实现 `hecate prompt by-label <label>` — GET /api/prompts/by-label/{label}

## 12. Memory 命令

- [x] 12.1 实现 `hecate memory blocks <agent_id>` — GET /api/agents/{id}/memory-blocks
- [x] 12.2 实现 `hecate memory blocks create <agent_id>` — POST /api/agents/{id}/memory-blocks，带 --label、--content
- [x] 12.3 实现 `hecate memory blocks update <agent_id> <block_id>` — PUT /api/agents/{id}/memory-blocks/{block_id}
- [x] 12.4 实现 `hecate memory blocks delete <agent_id> <block_id>` — DELETE 带确认
- [x] 12.5 实现 `hecate memory list` — GET /api/memory，用户记忆表格
- [x] 12.6 实现 `hecate memory search <query>` — GET /api/memory?q=<query>

## 13. 模板命令

- [x] 13.1 实现 `hecate template agents` — GET /api/agent-templates
- [x] 13.2 实现 `hecate template agents instantiate <id>` — POST /api/agent-templates/{id}/instantiate
- [x] 13.3 实现 `hecate template orchestration` — GET /api/orchestration-templates

## 14. Conversation 命令

- [x] 14.1 实现 `hecate conversation list` — GET /api/conversations，表格输出
- [x] 14.2 实现 `hecate conversation get <id>` — GET /api/conversations/{id}，显示消息

## 15. 模型命令

- [x] 15.1 实现 `hecate model list` — GET /v1/models，表格输出
- [x] 15.2 实现 `hecate model providers list` — GET /api/model-providers
- [x] 15.3 实现 `hecate model providers create` — POST /api/model-providers
- [x] 15.4 实现 `hecate model providers test <id>` — POST /api/model-providers/{id}/test

## 16. 消息命令

- [x] 16.1 实现 `hecate message citations <message_id>` — GET /api/messages/{id}/citations

## 17. 验证

- [x] 17.1 运行 `ruff check src/hecate/ tests/` — 零错误
- [x] 17.2 运行 `ruff format --check src/ tests/` — 零错误
- [x] 17.3 运行 `mypy src/` — 零错误
- [x] 17.4 运行 `python -m pytest tests/ -q` — 所有测试通过
