# P1 代码审查计划（协作模式）

## 审查目标
在进入 P2 之前，**逐步验证** P1 实现的正确性、完整性。

## 审查方式
- 我展示每个模块的代码和任务清单
- 你确认或提出问题
- 我解答或修复
- **逐个模块推进，不跳步**

---

## 审查流程

### 每个模块的审查步骤

1. **展示任务清单** — 列出该模块所有任务及完成状态
2. **展示关键代码** — 读取核心文件，展示实现
3. **运行相关测试** — 验证功能正确性
4. **确认或提问** — 你确认代码符合预期，或提出问题
5. **记录问题** — 如有问题，记录到本文档末尾
6. **进入下一模块** — 确认无误后继续

---

## 模块审查顺序

| 步骤 | 模块 | 任务数 | 核心文件 | 状态 |
|------|------|--------|---------|------|
| 1 | §1 项目骨架 | 8 | pyproject.toml, config.py, database.py | ⬜ |
| 2 | §2 数据模型 | 12 | models/*.py | ⬜ |
| 3 | §3 Graph DSL | 7 | engine/graph_dsl.py, compiler.py | ⬜ |
| 4 | §4 执行引擎 | 8 | engine/pregel.py, channel.py, checkpoint.py | ⬜ |
| 5 | §5 API 层 | 13 | main.py, api/**/*.py | ⬜ |
| 6 | §6 LLM 路由 | 6 | services/llm/*.py | ⬜ |
| 7 | §7 RAG 管线 | 9 | services/rag/*.py | ⬜ |
| 8 | §8 安全层 | 6 | services/security/*.py | ⬜ |
| 9 | §9 MCP 集成 | 4 | services/mcp/*.py | ⬜ |
| 10 | §10 端到端集成 | 10 | services/conversation.py, tests/ | ⬜ |
| 11 | §11 文档收尾 | 5 | README.md, AGENTS.md, CI | ⬜ |

---

## 审查检查项

### 代码质量
- [ ] 代码逻辑是否正确
- [ ] 是否符合设计文档
- [ ] 错误处理是否完善
- [ ] 类型注解是否完整

### 功能完整性
- [ ] 任务要求是否全部实现
- [ ] 边界情况是否处理
- [ ] 测试是否覆盖

### 代码风格
- [ ] 是否符合 AGENTS.md 规范
- [ ] 命名是否规范
- [ ] 注释是否必要

---

## 问题记录

| 编号 | 模块 | 问题描述 | 严重程度 | 状态 |
|------|------|---------|---------|------|
| - | - | - | - | - |

---

## 审查进度

- 开始时间：____
- 当前模块：§1 项目骨架
- 完成模块：0/11

---

## P1 实现清单

### §1 项目骨架（8 项）
- [x] 1.1 pyproject.toml
- [x] 1.2 src/hecate/ 目录结构
- [x] 1.3 core/config.py
- [x] 1.4 core/database.py
- [x] 1.5 docker/docker-compose.yml
- [x] 1.6 Dockerfile
- [x] 1.7 .env.example
- [x] 1.8 tests/conftest.py

### §2 数据模型（12 项）
- [x] 2.1 models/base.py
- [x] 2.2 models/agent.py
- [x] 2.3 models/session.py
- [x] 2.4 models/message.py
- [x] 2.5 models/tool.py
- [x] 2.6 models/knowledge.py
- [x] 2.7 models/skill.py
- [x] 2.8 models/conversation.py
- [x] 2.9 models/document.py
- [x] 2.10 models/checkpoint.py
- [x] 2.11 Alembic 迁移
- [x] 2.12 数据模型测试

### §3 Graph DSL + 编译器（7 项）
- [x] 3.1 engine/types.py
- [x] 3.2 schemas/graph-dsl.schema.json
- [x] 3.3 engine/graph_dsl.py
- [x] 3.4 engine/compiler.py
- [x] 3.5 三层 Agent 模板
- [x] 3.6 编译器错误处理
- [x] 3.7 Graph DSL 测试

### §4 执行引擎（8 项）
- [x] 4.1 engine/channel.py
- [x] 4.2 engine/checkpoint.py
- [x] 4.3 engine/worker.py
- [x] 4.4 engine/pregel.py
- [x] 4.5 interrupt/恢复机制
- [x] 4.6 子图执行
- [x] 4.7 engine/ports.py
- [x] 4.8 执行引擎测试

### §5 API 层（13 项）
- [x] 5.1 main.py (FastAPI app)
- [x] 5.2 core/deps.py (DI)
- [x] 5.3 /api/agents CRUD
- [x] 5.4 /api/sessions
- [x] 5.5 /api/tools
- [x] 5.6 /api/skills
- [x] 5.7 /api/knowledge-bases
- [x] 5.8 /v1/chat/completions
- [x] 5.9 /v1/models
- [x] 5.10 SSE streaming
- [x] 5.11 Rate Limiting
- [x] 5.12 API 集成测试
- [x] 5.13 /api/conversations

### §6 LLM 路由（6 项）
- [x] 6.1 services/llm/service.py
- [x] 6.2 streaming 响应生成器
- [x] 6.3 tool calling 协议
- [x] 6.4 模型降级策略
- [x] 6.5 /v1/models 模型列表
- [x] 6.6 LLM 服务测试

### §7 RAG 管线（9 项）
- [x] 7.1 services/rag/embedding.py
- [x] 7.2 services/rag/parser.py
- [x] 7.3 services/rag/chunker.py
- [x] 7.4 services/rag/indexer.py
- [x] 7.5 services/rag/searcher.py
- [x] 7.6 Knowledge Base 服务
- [x] 7.7 services/rag/storage.py
- [x] 7.8 documents 状态追踪
- [x] 7.9 RAG 管线测试

### §8 安全层（6 项）
- [x] 8.1 services/security/llm_guard.py
- [x] 8.2 PII 脱敏/还原
- [x] 8.3 NeMo Guardrails 配置
- [x] 8.4 安全中间件
- [x] 8.5 API Key 认证中间件
- [x] 8.6 安全层测试

### §9 MCP 集成（4 项）
- [x] 9.1 services/mcp/client.py
- [x] 9.2 MCP Tool 同步
- [x] 9.3 MCP Tool 调用
- [x] 9.4 MCP 集成测试

### §10 端到端集成（10 项）
- [x] 10.1 三层 Agent 模板编译
- [x] 10.2 完整对话闭环
- [x] 10.3 tool calling 完整流程
- [x] 10.4 RAG 检索集成
- [x] 10.5 端到端集成测试
- [x] 10.6 Agent + tool calling 测试
- [x] 10.7 Agent + RAG 测试
- [x] 10.8 interrupt/resume 测试
- [x] 10.9 fallback 测试
- [x] 10.10 Docker Compose 冒烟测试

### §11 文档收尾（5 项）
- [x] 11.1 README.md
- [x] 11.2 AGENTS.md
- [x] 11.3 CI 配置
- [x] 11.4 pre-commit hooks
- [x] 11.5 OpenAPI spec

---

## 审查报告模板

### 审查结论
- **通过** / **有条件通过** / **不通过**

### 发现问题
| 编号 | 模块 | 问题描述 | 严重程度 | 状态 |
|------|------|---------|---------|------|
| 1 | - | - | 高/中/低 | 待修复/已修复 |

### 技术债务
| 编号 | 描述 | 优先级 | 目标版本 |
|------|------|--------|---------|
| 1 | - | 高/中/低 | P2/P3 |

### P2 准备状态
- [ ] 所有高优先级问题已修复
- [ ] 技术债务已记录
- [ ] P2 依赖项已明确
