# Verification

## 基点与范围

基点 `a8b1aeed`(#302),分支 `feat/step10-evaluation-score-read-access-boundary`。本次在用户指定 worktree 实现与验证,未提交、未推送。入场已有 `.agents/skills/openspec-propose/SKILL.md` 无关差异,保留未动,应从本功能提交中排除。

## 实现与修正

- `GET /api/evaluation/scores` 按双路径实现:无 `task_id` 时 `NO_WORKSPACE`(403,含 system)先于 viewer 角色,以调用方 workspace 查询;带 `task_id` 时经既有 `_get_task_for_read` 解析(缺失/软删/跨 workspace → 既有 "Evaluation task not found" 恒定 404,非 system 缺 workspace 在查询前拒绝),viewer 校验后以 **Task 真实 workspace** 查询。service 零变化。
- 复用同包 `_forbidden` 并从 #300 引入 `_no_workspace` envelope;`_get_task_for_read`/`_not_found`/`svc.list_scores`/Task 写接口签名与行为零变化(AST 范围核对)。
- 测试修正记录:验证过程中修正了测试自身的四处笔误(种子签名重复传参、计数断言与种子数据不符、软删占位写成 disabled、超长行),均为用例问题,不涉及生产行为。

## GLM 实施阶段记录(审查前)

| 检查 | 命令 | 结果 |
|---|---|---|
| 边界 + 既有 + 邻接套件 | `python -m pytest tests/test_api/test_evaluation_score_access_boundary.py tests/test_api/test_evaluation_tasks_api.py tests/test_api/test_evaluation_task_access_boundary.py -q` | 48 passed |
| 完整 API + 评测 service 回归 | `python -m pytest tests/test_api tests/test_services/test_evaluation -q -n 4` | 1313 passed, 8 skipped, 0 failed |
| OpenSpec | `openspec validate step10-evaluation-score-read-access-boundary --strict` | 通过 |
| 共享检查(改动文件) | ruff check / format --check / mypy(2 个源文件) | 全部通过 |
| 共享检查(全仓) | `python scripts/dev.py check all` | ruff check 无错误;format 1629 files already formatted;mypy 962 source files 无错误 |
| 差异格式 | `git diff --check` | 通过 |

8 项跳过为仓库显式标记的 PostgreSQL 门控用例(Schedule API advisory locks、Security audit API session singleton),与本 change 的端点无关,不计为通过。

## 验收覆盖

- 双 workspace:无 `task_id` 查询仅返回本 workspace 分数,`total` 同条件;跨 workspace Task 的分数不可达。
- `NO_WORKSPACE`:system 与无 workspace 普通身份均 403 `NO_WORKSPACE`,查询前拒绝(不再产生 `IS NULL` 空结果),零副作用。
- 角色地板:本 workspace 无角色 → 403;viewer 可读。
- Task 可见性:缺失/软删/跨 workspace Task 的分数查询同体 404、零 ID 回显;修复前该形状为 200 空列表,本次有意对齐资源型查询的 404 契约。
- system scope:带 `task_id` 读取任意 workspace 可见 Task 的真实分数,约束到该 Task workspace。
- 语义保持:人工标注(`task_id=NULL` 行、`source=human` 过滤)、disabled Task 分数可读、`metric_name`/`status` 过滤、`created_at` 升序、分页与 total 全部保持。

## 范围与限制

- `svc.list_scores`、`get_task`/`_get_task_or_raise`、Task 写接口、报告接口(`evaluation_reports.py`,并行 change)、前端、Runtime 未修改。
- 本地使用共享 SQLite/ASGI fixtures;未运行真实 PostgreSQL 端到端与 CI(完整仓库 pytest 由 CI 执行)。

## Codex 审查修正与实际验证

重新 fetch 后 `origin/main` 与本分支 HEAD 均为 `a8b1aeed2f95abbf18f74c8b9b31287bdee24e43`。实际 worktree 为 `D:/Workspace/Github/xueyufish/.worktrees/step10-evaluation-score-read-access-boundary`(用户给出的 `xueyufish.worktrees` 路径不存在)。已核对本 worktree 解释器的 `hecate.__file__` 指向本 worktree 源码。

生产逻辑符合约定,无需额外修改。AST 对比确认本 change 只改动 `list_scores` 定义和 `_no_workspace` 导入,共享 helper、其余端点及 service 保持原状。

审查修正:

- delta spec 原 NO_WORKSPACE 场景漏写“不带 task_id”,与 system 按 Task 读取的契约冲突;已限定该场景,并明确 Task 范围的 viewer 门槛、system 角色豁免和非 system 缺 workspace 的查询前 404。
- design/tasks 中新增或复刻 `_no_workspace`、viewer 返回 403 等描述与实际实现不符,已修正;移除“整个评测域读取授权已完成”的过度声明,保留并行 sessions change 的独立范围。
- 原测试缺少 Task 范围角色拒绝、归属优先于角色、异常 workspace/已删除分数排除及完整错误 envelope,现已覆盖。缺 workspace 请求通过拦截 AsyncSession.execute 验证没有数据库查询,其余拒绝路径验证不调用 list_scores。
- 原排序断言只检查 value 排序,可能掩盖排序回归;现构造插入顺序、value 与 created_at 不同的样本,精确校验两页结果、总数和越界空页,覆盖全部既有过滤参数。
- 补齐 system 的空 workspace/不同 active workspace 两种 Task 查询,viewer/editor/admin 成功矩阵、合法空结果、disabled 在线 Task、人工标注审计字段和 source=human 兼容。成功和拒绝路径均比较 Task/Score 全列持久化快照,移除未使用的 registry stub/fixture 与 F401 suppression。

本轮命令均使用 `.venv/Scripts/python.exe`:

- 审查前相关回归: `-m pytest tests/test_api/test_evaluation_score_access_boundary.py tests/test_api/test_evaluation_task_access_boundary.py tests/test_api/test_evaluation_tasks_api.py tests/test_api/test_evaluation_annotations_api.py -q` → **60 passed**, exit 0。
- 审查补充后: `-m pytest tests/test_api/test_evaluation_score_access_boundary.py -q --tb=short` → **30 passed**, exit 0。
- `scripts/dev.py check all` → **全部通过**,Ruff check 无错误,format **1629 files already formatted**,mypy **962 source files 无错误**,exit 0。
- `openspec validate step10-evaluation-score-read-access-boundary --strict` → **通过**。
- `git diff --check` → **通过**。
- `-m pytest tests/test_api tests/test_services/test_evaluation -q -n 4 --tb=short -rs` → **1334 passed, 8 skipped, 20 warnings**,exit 0,347.56s。4 项 Schedule API 用例因 PostgreSQL advisory locks 显式跳过,4 项 tool_decisions API 用例因 PostgreSQL async_session_factory singleton 显式跳过;本次未新增 skip。warnings 为 FastAPI 422 常量及 protobuf datetime 的弃用提示。

本轮未提交、未推送,未修改其他 worktree。原有无关 skill 差异保留,应从功能提交中排除。
