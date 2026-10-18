# 验证记录 — step11-agent-evaluation-evidence-completeness

本记录替代最初 GLM 会话的验证描述。原实现的绑定套件 45 项通过，但未覆盖来源伪装、空范围空转、范围改写、豁免质量与不完整基线，不能以其通过推导上述边界安全。复核发现见 review.md。

## 静态与契约检查

在指定 worktree 使用 `.venv/Scripts/python.exe scripts/dev.py`（与 bash scripts/dev.sh 相同的共享入口）：

- check all：ruff check 通过；ruff format --check 为 1587 files already formatted；mypy 为 Success: no issues found in 941 source files。
- 新迁移单独 ruff check 和 format --check：通过。共享检查默认范围不包含 alembic。
- openspec validate step11-agent-evaluation-evidence-completeness --strict：valid。
- git diff --check：通过；publish_gate.py 无差异，共享 Workflow 评分算法未修改。

## 测试结果

| 范围 | 实际结果 |
|---|---|
| tests/test_api/test_agent_version_evaluation_binding.py | 76 passed |
| 下方相关回归命令 | 416 passed，2 条 FastAPI 旧状态名的 DeprecationWarning，无失败/跳过 |
| tests/test_migrations/test_upgrade_drift.py（独占临时 PG 库） | 1 passed，无跳过 |

```powershell
& .venv/Scripts/python.exe scripts/dev.py test tests/test_api/test_agent_version_evaluation_binding.py -q --tb=short
& .venv/Scripts/python.exe scripts/dev.py test tests/test_services/test_evaluation tests/test_api/test_agent_versioning.py tests/test_api/test_publish_gate_version_api.py tests/test_api/test_evaluation_tasks_api.py tests/test_api/test_evaluation_reports_api.py tests/test_services/test_orchestration/test_agent_execution_port.py tests/test_services/test_orchestration/test_agent_execution_port_prompt_override.py tests/test_services/test_prompt_optimization tests/test_layering_domain.py tests/test_layering_entry_imports.py -q --tb=short
```

核心回归包括：

- 十项中一项真实 evaluator 抛错或返回空输出：引擎保留逐项隔离、Run 完成，发布覆盖失败并准确定位；其余九项满分不能放行。
- 完整覆盖放行；有效低分属于质量失败而非缺证据；重复分数不能填另一个指标。
- human/llm_judge 同名分数、soft-deleted、foreign workspace 和越界数值不能完成确定性覆盖。
- 空/畸形/缺字段/未知版本范围、重复 id、非法豁免、零必需指标、全部豁免均拒绝，不因解析问题返回成功或 HTTP 500。
- known-bad 按冻结标记从覆盖和质量输入排除；额外样本高分不稀释必需样本低分，额外指标低分也不阻断本次范围。
- tags 筛选后仅冻结实际选中项，豁免按快照且限于选中项；LLM judge 错误保持信息用途，不成为新增门禁条件。
- 不完整基线或不同必需指标集不能报告无回归；原有完整基线正/负回归测试保留。
- 原范围遇到 tags/evaluator 重入冲突时在模型调用前失败，原范围不改写。
- warn/force/audit 和版本/workspace 绑定回归保留；读取 API 返回 scope 且不新增原始 agent_snapshot 暴露。
- 缺失总计完整保留，定位最多二十项；自定义 evaluator 不因同名自动获得内置支持。

## PostgreSQL 迁移与回退实测

使用已有 PostgreSQL 容器暴露的本地端口，但仅操作本次创建的独占临时库，不修改容器中其他项目/工作树数据库。复核临时库均已清理。

独立迁移探针使用 hecate_scope_review_20261018_d91e：

1. 空库 upgrade 到 017_agent_version_eval_binding；插入历史 evaluation_runs 行，再 upgrade head：成功，旧 evaluation_scope 为 SQL NULL。
2. 与应用 Base.metadata 比较所有 ORM 表/列的存在性：通过；另明确检查 evaluation_scope 为 nullable JSON。该检查不宣称验证所有旧列的类型或约束。
3. 无范围证据时 downgrade 017 → upgrade head：成功。
4. 写入 scope 后 downgrade 017：明确拒绝，原 scope 与 alembic_version=018_evaluation_scope 保留。
5. alembic heads：唯一 018_evaluation_scope (head)。

仓库原有 test_upgrade_drift.py 另在 hecate_scope_drift_20261018_a92f 执行：只把模块的测试库名替换为独占名称，DRIFT_ADMIN_URL/DRIFT_DATABASE_URL 指向同一容器。测试函数和断言未修改，完整迁移链及 ORM 表/列检查实际通过，临时库由原测试清理。

## 支持范围与合入门槛

当前覆盖 contains、exact_match、is_json、regex_match、pii_leakage 内置确定性指标。没有受支持必需指标的运行不能作为 require 完整证据。历史缺范围或格式不可核验的运行不补造，需重新评测。

正常低分仍使用原有阈值/公式；覆盖独立失败。Agent 的质量输入现在与冻结范围对齐；共享 Workflow、在线任务和未绑定执行不新增完整性门禁。外部/自定义 evaluator、完整 evaluator 版本和数据可比较性体系、Workflow repetitions、真实模型质量不在本切片。

本地检查通过，没有本切片的遗留阻断项；未提交、未推送，PR review 与 CI 仍是后续合入门槛。
