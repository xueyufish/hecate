# 验证记录 — step11-agent-version-evaluation-binding

本记录替代最初实施会话的验证描述。原实现曾通过绑定/API 套件，但没有覆盖任务重定向、预存答案跳过实际执行、Skill pin/L2、迁移双 head 等问题，不能据此宣称契约成立。复核发现和修正见 review.md。

## 共享检查

在本 worktree 的 Windows 开发环境使用 `.venv/Scripts/python.exe scripts/dev.py`，与 `bash scripts/dev.sh` 调用相同的共享入口。

- `check all`：ruff check 通过；ruff format --check 显示 1580 files already formatted；mypy `src/ packages/ scripts/dev.py` 显示 Success: no issues found in 934 source files。
- 新迁移单独执行 ruff check 和 format --check：通过（共享检查默认范围不含 alembic）。
- `openspec validate step11-agent-version-evaluation-binding --strict`：valid。
- `git diff --check`：通过；`git diff --exit-code -- src/hecate/ops/evaluation/publish_gate.py`：空差异，共享评分算法未修改。

## 测试实际结果

| 命令范围（统一通过 scripts/dev.py test，参数 -q --tb=short） | 结果 |
|---|---|
| test_agent_version_evaluation_binding.py + tests/test_services/test_skill/ + tests/test_tools/test_skill_versioning.py | 130 passed；其中绑定套件 36 项，涵盖最后补齐的 registry L2 加载 |
| tests/test_execution/test_entry_assembly.py + test_agent_version_evaluation_binding.py::test_workflow_agent_executes_pinned_workflow | 10 passed |
| 下方完整相关回归命令 | 515 passed，2 条 FastAPI 旧 HTTP 状态名的 DeprecationWarning，无失败或跳过 |

相关回归命令：

```powershell
& .venv/Scripts/python.exe scripts/dev.py test tests/test_services/test_evaluation tests/test_api/test_agent_versioning.py tests/test_api/test_publish_gate_version_api.py tests/test_api/test_evaluation_tasks_api.py tests/test_api/test_evaluation_reports_api.py tests/test_api/test_workflow_version_resolution.py tests/test_services/test_orchestration/test_agent_execution_port.py tests/test_services/test_orchestration/test_agent_execution_port_prompt_override.py tests/test_services/test_skill tests/test_tools/test_skill_versioning.py tests/test_services/test_prompt_optimization tests/test_layering_domain.py tests/test_layering_entry_imports.py -q --tb=short
```

这些测试范围有重叠，不相加为唯一测试总数。最终 SkillLoader/registry 修正后重新执行其受影响的 130 项及入口 10 项；最终模型/persona 入口透传由 workflow 路径用例再次验证。

核心行为覆盖：

- v1 高分不能放行 v2；匹配版本评分通过/失败分别放行/阻断；NULL 历史及跨 workspace Run 被排除。
- 改任务 Agent/版本、删除任务、改草稿不改变旧 Run 身份；配置哈希不匹配被排除；已完成 Run 不可重跑，绑定冲突不能覆盖旧证据。
- 有预存答案仍实际调用冻结模型与参数、persona、Skill pin；全链 engine→production RuntimePort adapter→LLM 替身→确定性 evaluator→publish 成功。
- offline runner 实际执行体透传版本并保存身份和数据集快照；任务更新保留/修改/显式清空版本；不适用来源和 online 任务拒绝版本声明。
- 版本缺失、Agent 删除、跨 workspace 数据集/任务、空数据集和生成失败不能成为完成证据。
- 冻结工具名单可使用已从草稿移除的工具；缺失 pin/知识库及 foreign 工具拒绝；workflow Agent 的冻结模式和精确 workflow pin 送达既有 EntryExecutionService。
- registry 的 load_skill 在草稿取消绑定后仍加载 pin 内容；新草稿技能不在冻结名单中时拒绝加载。
- drift-only 门禁仍强制版本匹配；回归读取已发布版本基线并处理缺基线；数据集无变化/变化/缺快照分别放行/拒绝/拒绝。
- warn 如实报告而不阻断；force 报 bypass 非通过，审计失败不移动发布指针。

## PostgreSQL 迁移实测

使用已有 PostgreSQL 容器中独立创建的临时数据库 `hecate_step11_review_20261018_c8d1`，未改动其他工作使用的数据库，验证结束已删除该临时库。

1. `alembic heads`：唯一 `017_agent_version_eval_binding (head)`，父节点为 `7a6e5d4c3b20`。
2. 空库执行 `alembic upgrade 7a6e5d4c3b20`，插入历史 evaluation_runs 行，再执行 `alembic upgrade head`：成功，历史四个绑定字段全部 SQL NULL。
3. 仅有未绑定历史行时 `alembic downgrade 7a6e5d4c3b20` → `alembic upgrade head`：均成功。
4. 写入 Agent、版本、hash 和 snapshot 后执行同一 downgrade：明确报 Cannot downgrade: version-bound evaluation evidence must be retained；随后查询确认绑定证据和 alembic_version 均保留。
5. ORM 与迁移的两个新增索引名称一致。应用回退保留增量列，有证据时不自动删列。

## 支持范围与后续门槛

本地验证证明此版本绑定切片，不代表完整 step11 发布平台完成。chat 覆盖实际适配器调用（LLM 为替身）；workflow pin 覆盖真实解析和入口透传，既有 workflow graph 选择回归同时通过；未验证真实模型质量或外部 provider。模型参数沿现有入口支持面传递，不新增 Runtime 参数接口。

技能内容有 pin 时冻结；技能治理/发现池、未 pin 技能内容、Memory policy、工具实现及知识库数据沿用既有 live 契约。此次不含签名、部署激活、灰度、完整发布组合或知识库内容快照。

当前没有本切片的遗留阻断项。未提交、未推送、未创建 PR；PR review、CI 和合入后迁移链复核仍是后续合入门槛。本地测试不替代 CI。
