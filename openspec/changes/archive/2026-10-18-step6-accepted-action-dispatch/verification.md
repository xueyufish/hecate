# Verification

## 相关完整回归

Windows worktree 解释器直接运行共享 scripts/dev.py，与 bash scripts/dev.sh 使用同一实现。

```text
.venv/Scripts/python.exe scripts/dev.py test packages/hecate-runtime/tests packages/hecate-durable/tests tests/test_runtime/test_action_hook.py tests/test_runtime/test_g2_recovery_states.py tests/test_runtime/test_tool_receipts.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_runtime/test_kernel_purity.py tests/test_layering_domain.py -q
344 passed, 6 skipped in 83.40s
```

显式收集 packages/，不依赖默认 testpaths。此命令移除进程内 DURABLE_TEST_POSTGRES_URL，保证旧 durable fixture 只作用于每例独有 SQLite 文件，不让其 drop_all 触碰共享 PostgreSQL。6 个跳过为原 accepted-decision PG 专用 5 项和 worker_dispatch 中仅 PostgreSQL 并发事务可运行的 1 项；本次不把它们记作通过。

## 新派发 SQLite/PostgreSQL 矩阵

```text
DURABLE_TEST_POSTGRES_URL=<existing local test PostgreSQL>
.venv/Scripts/python.exe -m pytest packages/hecate-durable/tests/test_accepted_action_bridge.py -q --tb=short
26 passed in 21.93s
```

新文件仅使用 decision_database 隔离 helper，SQLite 每例独有文件，PostgreSQL 每例创建独有 schema、只清理自身 schema。未对共享 public 表执行 drop_all。两方各 13 项覆盖实际已接受决策、结果复用、缺/错/过期/接管 owner、错误 hook Run/Task、readonly 审批期间过期、回执写入失败、两个 SQL hook 的并发以及独立进程恢复。

独立进程包含真实业务替身效果计数（fsync 后恰一行）、原 execution_id、原结果内容、读进程模型零调用与工具零调用。没有用内存对象共享代替持久保证。

首次 PG 初始化因工作树未安装 psycopg 报错；使用仓库声明的 `.venv/Scripts/python.exe scripts/dev.py sync --profile postgres`（uv --locked）安装已有 postgres extra 后重跑通过。未修改 pyproject 或 lock 文件，未新增临时依赖。

## 共享检查

```text
.venv/Scripts/python.exe scripts/dev.py check all
ruff check: All checks passed!
ruff format --check: 1596 files already formatted
mypy: Success: no issues found in 949 source files
```

新增测试替身的签名与 Optional 结果索引问题通过正常签名/非空断言修复，没有类型忽略或降低 gate。

## 规范与工作区

```text
openspec validate step6-accepted-action-dispatch --strict
Change 'step6-accepted-action-dispatch' is valid
git diff --check
exit 0
```

新代码和 artifacts 仍未跟踪，因此额外逐行检查其尾部空白。未提交、推送或归档；CI 和 PR review 不由本地结果替代。
