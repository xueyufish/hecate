# 验证记录 — step11-agent-publication-receipt

本记录为复核修正后的实际结果，取代初版实施记录。复核前凭据套件 10 passed；新增回归首次执行为 7 failed / 13 passed，复现租户身份、发布归属、判定配置快照及未消费 baseline 被记录的问题。现均已修复。

## 共享检查

- `.venv/Scripts/python.exe scripts/dev.py check all`：ruff check 通过，ruff format --check 1595 files already formatted，mypy 946 source files 零错误。
- `openspec validate step11-agent-publication-receipt --strict`：valid。
- `git diff --check`：通过。
- `git fetch origin main` 后 origin/main 为 `42035439`，本分支基点 `deae7797`。仅落后 step14 只读 Run action 查询提交，无本 change 的文件交集；未对未提交 worktree 执行 rebase。

## 核心与迁移护栏

命令：`.venv/Scripts/python.exe scripts/dev.py test tests/test_api/test_agent_publication_receipt.py tests/test_migrations/test_agent_publication_receipt.py -q --tb=short`

结果：**29 passed**，其中凭据 26 项、迁移护栏 3 项。

- actual candidate id/hash 与发布版本 hash、配置及报告快照保留；后来修改任务 JSON（替换后 flush/refresh 确认持久化）、门禁或补跑评测不改写历史。
- 判定过程中修改门禁并新增候选运行，凭据仍保留判定前的配置与原运行，不二次查询 latest。
- baseline 仅启用回归时记录，关闭时字段为 NULL；require 缺证据拒绝且无成功凭据。
- 四种 outcome；force 参数在没有实际绕过时仍为 passed；bypass 审计按 action/resource/operator/version/report 精确对应凭据。
- 真实 NOT NULL SQL 约束失败（非构造器抛错）覆盖普通与 force 发布；请求事务回滚后指针、凭据和已 flush 的 bypass 审计均无残留。
- 缺 workspace 的 JWT/SSO/LDAP/workspace API key：历史与发布均 404；显式 system API key 可访问；跨 workspace 发布不能写凭据。
- 历史读取不重算门禁、返回字段白名单、分页边界 422、重复发布追加两条凭据、rollback 不追加、历史指针无凭据时不补造。
- ORM CHECK 拒绝未知 outcome；迁移 downgrade 对 live/soft-deleted 凭据均拒绝，空表允许执行删除操作。

## 相关回归

命令：

```text
.venv/Scripts/python.exe scripts/dev.py test tests/test_api/test_agent_publication_receipt.py tests/test_migrations/test_agent_publication_receipt.py tests/test_api/test_agent_version_evaluation_binding.py tests/test_api/test_agent_versioning.py tests/test_api/test_publish_gate_version_api.py tests/test_api/test_evaluation_tasks_api.py tests/test_services/test_evaluation tests/test_layering_domain.py tests/test_layering_entry_imports.py -q --tb=short
```

结果：**428 passed，2 warnings**。警告为 dataset_versions 及 FastAPI 使用已弃用 HTTP 422 常量，未修改这些无关路径。428 已包含上述 29 项，不重复累加。

## PostgreSQL 实跑

使用本地 PostgreSQL 16 的独立临时数据库，不占用并行 worktree 的共享 drift 库；两次验证完成后各自删除临时库。

- 仓库 `tests/test_migrations/test_upgrade_drift.py`：**1 passed**。全链 upgrade head 与全部 ORM 表/列比对通过；单一 head 为 `019_agent_publication_receipt`。
- 先升级 018，插入历史 Agent、版本及 published_version=1，再升级 head：原指针/版本保留，publication 表为空，无回填。
- 空凭据表 downgrade 018 / upgrade head：通过。
- 实际迁移表拒绝非法 outcome（CheckViolationError）。
- 有凭据后 downgrade 018：按预期拒绝；凭据内容、发布指针与 alembic revision 保持不变。

## 边界

未提交、未推送、未归档；未改公共 conftest、评测引擎、Task/Run、Runtime/durable 或全局功能清单。CI 全套和 PR review 尚未运行，需在后续提交/PR 流程完成。

本次封闭发布凭据写入口和历史读入口的租户边界；其他既有版本 CRUD 的 workspace 校验应另立 change 统一治理。内部 service 无 auth_context 的既有调用视为受信调用，API 必须提供真实 AuthContext。
