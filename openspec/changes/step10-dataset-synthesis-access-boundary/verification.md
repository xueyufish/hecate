# Verification — step10-dataset-synthesis-access-boundary

基点：`origin/main` = `d10a6cb8cae795c96baa8d0b291fbaebd66d94ec`（分支 `feat/step10-dataset-synthesis-access-boundary`）。以下原始结果由 GLM 报告；Codex 独立复跑与修复证据见文末，二者不混作同一轮验证。未提交、未推送。

## GLM 实施阶段报告(审查前)

| 套件 | 结果 |
|---|---|
| `tests/test_api/test_synthesis_access_boundary.py`（新增边界套件） | **33 passed** |
| `tests/test_services/test_evaluation/test_synthesis/`（含 8 个新增种子校验/失效传播测试） | **46 passed** |
| 既有回归 `test_synthesis_api.py` + `test_synthesis/`（任务 3.2） | **54 passed，零修复** |
| 格式化后复跑三套件合计 | **87 passed** |
| 全量 `pytest tests/ -q -n 4` | **7477 passed，2 failed，103 skipped，1 xfailed**（26 分 06 秒） |

### GLM 对全量回归中两个失败的原调查说明(未独立复现同等全仓条件)

1. `tests/test_execution/test_approval_wait_park.py::test_crash_window_waiting_approval_without_contract_fails_closed` — 隔离复跑**通过**（`-n 4` 并行抖动）。
2. `tests/scenarios/test_sc05_managed_wake_chain.py::test_sc05_wake_effect_upload_loss_retries_without_double_apply[sqlite]` — 工作树隔离复跑失败发生在机器高负载期（其他会话并行测试）；干净基点 `d10a6cb8` 复跑**通过**，工作树空闲后复跑**通过**（负载抖动）。

两测试均属 `test_execution`/`scenarios` 域，与合成域无代码路径交集；变更文件清单见下。

## 质量门禁

- `python scripts/dev.py check all`：**全绿**（ruff check、ruff format——5 个改动文件已重排、mypy 962 文件零问题；该共享命令不含 commitizen）。
- `git diff --check`：**干净**。

## 变更范围（git status）

- 修改：`src/hecate/ops/api/synthesis.py`、`src/hecate/ops/evaluation/synthesis/service.py`、`tests/test_services/test_evaluation/test_synthesis/test_job.py`、`tests/test_services/test_evaluation/test_synthesis/test_service.py`
- 新增：`tests/test_api/test_synthesis_access_boundary.py`、`openspec/changes/step10-dataset-synthesis-access-boundary/`
- **`job.py` 零改动**：种子失效经 `_execute_job` 既有 `except Exception → _mark_failed` 路径自然传播（`SeedDatasetUnavailableError` 的消息即显式 error_message），设计中的"视需要微调"无需触发。
- 未触碰在途 Task 管理包任何文件（`evaluation_tasks.py`、`test_evaluation_task_*` 等均不在变更清单中）。

## 边界覆盖摘要（对应 spec scenarios）

- 非 system 发起锚定自身 workspace（省略/相等/foreign 三态，foreign 恒定 FORBIDDEN 且不查询目标存在性）。
- `NO_WORKSPACE`：非 system 缺 workspace、system 未选/选了不存在或已删除 workspace，全部零副作用。
- system 显式选择目标 workspace，Job 行落该 workspace（全列快照断言）。
- EDITOR 写闸门（viewer/role-less 403，两种报错文案精确断言）。
- 种子可见性先于角色（4 类失效 × 3 档角色全部恒定 Dataset 404、无 ID 回显、模型零调用）；可见种子 + viewer = 403（顺序证明）。
- 样本归属锚定父 Dataset：零 UUID/他 workspace 历史 item 仍为合法种子（service 层 `_load_seeds` + 同步全链路 200 用例）。
- pipeline 执行期复验：排队后种子删除 → `_run_pipeline` 抛异常（无目标 Dataset、模型零调用）、`_execute_job` → `status=failed` + error_message 指向种子、同步路径竞态 → 恒定 404（`validate_seed` 二次调用注入）。
- job 轮询：缺失/软删/跨 workspace 恒定 404、无 workspace 查询前 404、viewer 通过 / role-less 403、system 跨租户 200 + 完整响应结构。
- 语义保持：200/202 分界、响应结构（sync 4 字段、async 2 字段、job 12 字段）、tags、`422` 不变。

## 剩余风险

- 本轮全量回归的 103 skipped 含 PostgreSQL 条件测试（未配置 `DURABLE_TEST_POSTGRES_URL`），真实 PG 回归留待 CI/review 环境。
- 后台 pipeline 运行中途（非排队后）种子被删不在本边界威胁模型内（运行中快照语义属后台任务恢复机制重构，非目标）。
- 全量回归耗时 26 分钟（`-n 4`，机器同时承载其他会话负载），两次失败已逐一定位为环境抖动。

## Codex 审查与修复

### 范围与基点

- 目标为已登记的 `D:/Workspace/Github/xueyufish/.worktrees/step10-dataset-synthesis-access-boundary`,分支 `feat/step10-dataset-synthesis-access-boundary`,完整未提交实现可审查。HEAD 和交回任务的基点均为 `d10a6cb8cae795c96baa8d0b291fbaebd66d94ec`。
- 审查中已 fetch origin,最新 `origin/main` 为 `4917d055385437545c70a6d6930e20a5b95ec5be`,分支落后两个提交。main 增量为 review skill 与 Task 管理边界,未修改本包源码/主 spec/合成测试路径;共享 evaluation/auth helpers 未变。没有 rebase 或切换分支。
- 审查入口已有四个已修改跟踪文件、一个新 API 测试和一个未跟踪 OpenSpec change 目录。change 目录由 GLM 提前生成,本次保留既有内容,没有执行任何 OpenSpec CLI/skill 或创建 change artifacts。按原交接要求直接更新已有 `openspec/specs/dataset-synthesis/spec.md`。
- 已确认 Python 从目标 worktree 加载 hecate 源码,而不是 main checkout。

### 修复前发现与处理

1. **P1** `src/hecate/ops/evaluation/synthesis/service.py:121-130`(修复前):先创建/flush target Dataset,再在 `_load_seeds` 复验种子;若此时种子不可用,后台 `_mark_failed` 会提交未回滚事务,留下没有 job target 引用的空 Dataset。新增回归先复现目标数为 1 而非 0,现将种子验证和读取全部放在 target 创建前;未修改一般 LLM 失败时保留部分成果的既有契约。
2. **P2** `src/hecate/ops/api/synthesis.py:241`(修复前):system 携带另一 active workspace 时,轮询仍按该 workspace 过滤,合法跨 workspace job 返回 404。新增回归先复现 404 而非 200,现 system 始终以 None 作内部 job 查询过滤,普通身份仍用自身 workspace。
3. **P2** `tests/test_api/test_synthesis_access_boundary.py:488-509`(修复前):所谓同步竞态测试在首次 API validate_seed 即抛错,没有测试 pipeline 重验及 `_run_sync` 的 404 映射。现于第二次校验注入失效,确认进入执行期检查、无持久化副作用。
4. **P2** 主 `openspec/specs/dataset-synthesis/spec.md` 在交回时未更新,只有额外生成的 change delta。现直接补齐主 spec 的合成/轮询权限、种子归属、失败和兼容性契约。
5. **P3** `tests/test_services/test_evaluation/test_synthesis/test_job.py:137,168,182,186`(修复前):Job fixture 标注为 object 后新增三处 type: ignore。现返回具体 DatasetSynthesisJobModel,移除新增类型抑制。

补充覆盖:拒绝路径默认禁止后台调度;job 可见性先于角色(含 role-less);缺 workspace 通过拦截 AsyncSession.execute 证明查询前拒绝;同步目标 Item workspace;编辑者/system 从 202 接受经真实 worker pipeline 到 completed/轮询的 Dataset、Item、Job 归属与指标(仅 stub 外部 LLM/DLP 和调度)。

### 本轮独立验证

所有命令在目标 worktree 使用 `.venv/Scripts/python.exe`:

- 审查前: `scripts/dev.py test tests/test_api/test_synthesis_access_boundary.py tests/test_api/test_synthesis_api.py tests/test_services/test_evaluation/test_synthesis -q --tb=short` → **87 passed**,exit 0。
- 修复前新增复现:system 轮询参数化测试 + seed-read failure orphan 测试 → **2 failed, 1 passed**。失败分别为 404 != 200、target_count 1 != 0。
- 首轮修复针对性回归:system polling、同步执行期失效、TestSeedUnavailablePropagation → **6 passed**,exit 0。
- 修复与完整覆盖后同一合成范围回归 → **94 passed**,exit 0。
- 对 GLM 全仓报告的两个失败进行隔离复跑: `scripts/dev.py test tests/test_execution/test_approval_wait_park.py::test_crash_window_waiting_approval_without_contract_fails_closed tests/scenarios/test_sc05_managed_wake_chain.py::test_sc05_wake_effect_upload_loss_retries_without_double_apply[sqlite] -q --tb=short` → **2 passed, 2 warnings**,exit 0。没有独立复现 GLM 的同等并发全仓条件,不据此断言原失败原因或全仓零失败。
- `scripts/dev.py check all` → **Ruff check/format 通过(1633 files already formatted),mypy 962 source files 零问题**,exit 0。
- `scripts/dev.py test tests/test_api tests/test_services/test_evaluation -q -n 4 --tb=short -rs` → **1440 passed, 8 skipped, 20 warnings**,exit 0,178.30s。8 项为既有 Schedule API advisory locks 和 Security audit API session singleton 的 PostgreSQL 依赖跳过,本轮未新增 skip。warnings 为 FastAPI 422 常量和 protobuf datetime 的弃用提示。
- `git diff --check` → **通过**。

### 交回与限制

**审查结论:ready with follow-up。** 已确认的 P1-P3 均已修复,合成/扩展 API 与 evaluation service 回归、共享检查及差异检查通过。集成者更新到最新 main 后,需重跑合成测试及 `tests/test_api/test_evaluation_task_management_write_access_boundary.py` 和既有 Task API 回归;该新 main 测试文件不在当前旧基点,本轮没有将其拷入或假称已跑。CI 全仓/平台矩阵、真实 PostgreSQL 以及 PR review 仍待后续。

仅在目标 worktree 修复,未提交、未推送、未建立 PR、未修改其他 worktree。现有 OpenSpec change 目录已保留;本次普通开发没有激活 OpenSpec 流程。

集成后建议复跑(由集成者更新 main 后执行,本轮未执行新 main 专属测试):

```powershell
.venv/Scripts/python.exe scripts/dev.py test tests/test_api/test_synthesis_access_boundary.py tests/test_api/test_synthesis_api.py tests/test_services/test_evaluation/test_synthesis tests/test_api/test_evaluation_tasks_api.py tests/test_api/test_evaluation_task_management_write_access_boundary.py -q
.venv/Scripts/python.exe scripts/dev.py check all
```

最终跟踪文件变更为两个合成源码文件、两个合成 service/job 测试和主 dataset-synthesis spec;未跟踪内容为新增 API 边界测试及 GLM 原有 change 目录。源码 `synthesis/job.py`、公共模型、迁移、依赖及其他 worktree 均未修改。

## GLM 独立复核（审查后）

对 Codex 全部修改逐项读码验证：P1（种子读取晚于建目标 → `_mark_failed` 提交空 Dataset）与 P2（system 携带 active workspace 时轮询被等值过滤）均为真实缺陷，修复正确；竞态测试现于第 2 次 `validate_seed` 调用（pipeline 执行期）注入失效并以 `n == 2` 断言证明进入复验；新增覆盖（`AsyncSession.execute` 拦截证明缺 workspace 查询前拒绝、system+active workspace 轮询回归、202 → 真实 worker pipeline → completed 的全链路归属/指标、拒绝路径禁止后台调度）逐一读码确认。主 spec 与 change delta 的 requirement 内容逐字一致（仅空行差异）。

独立复跑（同 worktree，`.venv/Scripts/python.exe`）：

| 套件 | 结果 |
|---|---|
| 合成范围（boundary + API + service/job） | **94 passed**（与报告一致） |
| `tests/test_api` + `tests/test_services/test_evaluation` `-n 4` | **1440 passed, 8 skipped**（与报告一致） |
| `scripts/dev.py check all` | **全绿**（ruff format 1633 files、mypy 962 files 零问题） |
| `git diff --check` | **通过**（CRLF 提示为 Windows 工作副本常态，提交时按仓库规则归一化） |
| 全量 `pytest tests/ -q -n 4` | **7485 passed, 1 failed, 103 skipped, 1 xfailed**（19 分 43 秒） |

全量唯一失败 `test_sc05_wake_chain_succeeds_over_real_http[sqlite]` 与上轮失败的 `upload_loss_retries[sqlite]` 同属一个场景文件，隔离复跑**通过**（xdist 并发下 sqlite real-HTTP 场景的计时抖动；两轮失败的参数化用例隔离后均绿，且与合成域无代码交集）。

archive 注意事项：主 spec 已被审查方直接更新且与 delta 内容一致，届时执行 `/opsx-archive` 的 spec 同步前需先还原 `openspec/specs/dataset-synthesis/spec.md` 的工作副本改动（`git checkout -- <file>`），让 delta 经既有 sync 流程套用，避免 requirement 重复添加。
