# Tasks

## 1. Guard 修复

- [x] 1.1 `tests/test_layering_domain.py`：`_is_other_domain_import`
  入口 `module.removeprefix("hecate.")` 归一化
- [x] 1.2 新增 matcher 单元回归：`"hecate.ops.x"` → `"ops"`、
  `"ops.x"` → `"ops"`（裸前缀兼容）、`"hecate.core.x"` / `"hecate.runtime.x"`
  → `None` 负例

## 2. runtime → 域 收敛（先于 2.5 probe 白名单）

- [x] 2.1 `runtime/agent_execution_port.py`：handoff 3 函数 import
  下沉到 139 / 190 / 207 三个调用方法内（design D2）
- [x] 2.2 `runtime/security/egress.py`：DLPFinding + DLPScanner 进
  TYPE_CHECKING；DLPAction 在 action 映射方法内函数级 import（design D3）
- [x] 2.3 `runtime/security/hooks/output_security.py`：374 行
  isinstance 处函数级 import，模块级 import 行删除（design D4）
- [x] 2.4 `tests/test_runtime/test_runtime_self_sufficiency.py`：
  CORE_RUNTIME_MODULES +3（design D9）
- [x] 2.5 验证：guard 三件套全绿
  （`pytest tests/test_layering_domain.py
  tests/test_runtime/test_runtime_self_sufficiency.py
  tests/test_build/test_core_self_sufficiency.py -q`）

## 3. 域 → 域 收敛

- [x] 3.1 `channel/api/v1/agents.py`：session_lock_manager 函数内
  import（design D5）
- [x] 3.2 `channel/api/v1/chat.py`：session_lock_manager +
  WorkflowExecutionService + ToolRegistry 三符号 lazy 化
  （`_build_tool_registry` 注解字符串化）（design D5）
- [x] 3.3 `channel/management/alerts.py`：新增 `_alert_service(db, ctx)`
  helper（函数内 import），~10 处调用点改走；NotificationDispatcher
  279 行就地 lazy（design D6）
- [x] 3.4 `ops/notification.py`：NotificationChannelAdapter 进
  TYPE_CHECKING；3 个具体 adapter 下沉进 `_build_adapter_map`
  （design D7）
- [x] 3.5 `ops/ops_center/conversation_messages.py`：
  derive_session_messages 下沉到 61 行使用函数（design D8）

## 4. 文档 ride-along

- [x] 4.1 `channel/management/__init__.py` docstring 刷新（pre-Wave-1
  措辞删除，如实描述现状）
- [x] 4.2 `runtime/AGENTS.md`：sanctioned lazy import 清单扩为全量
  （2 → 5 条），写明双层防线覆盖方式（design D10）
- [x] 4.3 research doc（gitignored，主 checkout 本地改）：进度表
  Phase R 行 "#117–#120" → "#117–#122"（design D10；不进 commit）

## 5. Verify

- [x] 5.1 全量四件套：`ruff check src/ tests/` + `ruff format --check
  src/ tests/` + `mypy src/` + `pytest tests/ -q` 全绿
- [x] 5.2 冒烟：`python -c "import hecate.main"`
- [x] 5.3 反向验证：临时放一个 studio 内模块级
  `from hecate.ops.audit import service`，guard 必须红（验证闸门真实
  生效后删除）
- [x] 5.4 全库复扫：修正版匹配器跨域模块级 import 命中数 = 0

## 6. Commit & PR

- [ ] 6.1 单 commit `fix(guard): make domain layering guard actually
  match hecate.-prefixed imports + conform 12 violations`，body 覆盖：
  matcher bug 机制、12 处处置清单、probe 白名单 +3、D10 文档项
- [ ] 6.2 Push（用户批准 per AGENTS.md）
- [ ] 6.3 Open PR；Squash and merge
- [ ] 6.4 After merge: research doc §3.7 补 PR 号 + 归档本 change；
  删 worktree/分支（`./scripts/opsx-flow.sh delete layering-guard-fix`）
