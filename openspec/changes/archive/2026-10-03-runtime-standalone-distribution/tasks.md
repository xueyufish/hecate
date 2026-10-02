# Tasks

任务组 1—4 对应 design D6 的四批 PR;组 5 收尾。每批 PR 合入前独立全绿,不以组间顺序阻塞已绿批次的回归。

## 1. 包骨架与内核迁移(PR1,行为零变化)

- [x] 1.1 前置核查 `pregel.py` 对 `runtime.temporal` 的引用形态;若为模块级,先改为注入点并补测试(阻塞项,不允许带病迁移)。验证:grep 证据 + 引用点测试通过
- [x] 1.2 创建 `packages/hecate-runtime/`(pyproject: hatchling、无 `hecate` 依赖、extras 占位;README 登记反向依赖禁令与 shim 退出条件),迁移 `src/hecate/runtime/` 全部内核模块到 `src/hecate_runtime/` 并脚本重写内部 import(`hecate.runtime.*` → `hecate_runtime.*`);`runtime/api/hooks.py` 迁至 `src/hecate/ops/api/`、`runtime/temporal/` 迁至 `src/hecate/execution/temporal/`,`main.py` 挂载路径同步。验证:内核源码 `grep -c "hecate\.runtime"` 归零、全量 pytest 绿
- [x] 1.3 `src/hecate/runtime/` 改为薄 shim(逐模块 re-export),根 pyproject 主应用依赖加 `hecate-runtime`、workspace members 覆盖;分层测试加 shim 纯度检查(仅 import/re-export)。验证:既有 `tests/test_runtime/`、`tests/test_services/test_workflow/` 不改断言全绿;`tests/test_execution/` 契约样本不变

## 2. core 解耦(PR2)

- [x] 2.1 以 grep 证据固定 RuntimeConfig 字段清单(5 处模块级 + `context_processors` 9 处 + `task_memory_hook` 5 处 + `compaction` 2 处引用的配置消费面),新建 `hecate_runtime/config.py`(`RuntimeConfig` dataclass,默认值可独立构造);对 settings 默认值做快照对照。验证:字段清单表落入 PR 描述,快照测试通过
- [x] 2.2 改造内核引用:`compaction`/`security/encryption`/`security/llm_guard`/`context_processors`/`task_memory_hook` 的 `hecate.core.config`/`core.database`/`core.composition` 引用改为 RuntimeConfig 注入或注入点;`execution_assembly.assemble_execution` 增加 `config: RuntimeConfig | None` 参数。验证:内核源码 `grep "hecate\.core"` 归零;注入等价配置后既有断言不变(spec: Injected configuration preserves behavior)
- [x] 2.3 主应用装配处(chat 引擎链、`execution/builtin.py`、评估链)从 `settings` 映射构造 RuntimeConfig 注入。验证:三条链路的既有集成测试全绿

## 3. 懒加载行处置与可选能力声明(PR3)

- [x] 3.1 清除/改造四行:`tool_access` 改注入接口(默认 no-op)、`tool_worker` builtin 名称集入内核常量、`coordinator_worker` 模板构造参数化、`task_memory_hook` 写回改经 Memory Provider Protocol 并删除 ORM import。验证:四行从 sanctioned 清单移除,各自的行为测试通过
- [x] 3.2 可选能力声明:`hecate-runtime` extras 定 `memory`(→hecate-memory)与 `sandbox`(→hecate-sandbox);装配入口提供 `capability_status()`,未安装 extras 或未注入(A2A)时启动声明 unsupported;核对 `security/*` 对 `ops.dlp`/`findings_writer` 无函数级残留。验证:未装 extras 的装配测试得到显式 unsupported 而非 ImportError(spec: Missing optional extra surfaces unsupported)
- [x] 3.3 `test_runtime_self_sufficiency.py` 升级:保留 shim 路径探测;新增 `hecate_runtime` 直接探测(blocked = 全部 `hecate.*` 前缀,放行 `hecate_runtime`);sanctioned 清单同步到新包路径。验证:探针双模式通过;临时在内核加 `hecate.core` import 时探测失败(负例验证后还原)
- [x] 3.4 更新 `src/hecate/runtime/AGENTS.md`(迁至 `packages/hecate-runtime/AGENTS.md` 或双留指针):懒加载清单逐行标注处置结果与剩余退出条件、shim 说明、反向依赖禁令。验证:清单与代码状态一一对应

## 4. wheel 构建与 CI 干净安装(PR4)

- [x] 4.1 编写 `packages/hecate-runtime/smoke/smoke_run.py`(stub 模型执行一次图,断言事件序列,仅依赖已装包);本地 `uv build --package hecate-runtime` 产物在新 venv 安装并跑通冒烟(Windows 本机先验)。验证:干净 venv 冒烟通过,无源码路径依赖
- [x] 4.2 CI 新 job:构建非 editable wheel → 新 venv 安装 → 运行冒烟;workspace sync 列表加 `--package hecate-runtime`;不跑全量测试。验证:CI 配置 lint 通过(本地模拟步骤与 step2-review-hardening 的 CI 基线表达式同法核对)
- [x] 4.3 文档:包 README(安装、extras、能力声明、已知边界)、根 AGENTS.md 无需新增规则(核对后确认)、独立基线 §2/§3 标注"5b 已交付 wheel,安装性验证证据指针"。验证:文档引用路径可解析

## 5. 验证与收尾

- [x] 5.1 四项本地门:`ruff check src/hecate/ tests/ packages/ scripts/feature_inventory.py`、`ruff format --check`、`mypy src/`(兼容 shim 后应零错误)、`python -m pytest tests/test_runtime/ tests/test_execution/ tests/test_services/test_workflow/ -q` 全绿。验证:0 错误
- [x] 5.2 `openspec validate runtime-standalone-distribution --strict` 通过;feature-inventory 增量准入(`check --base-ref`)对新条目(若有)零错误。验证:命令输出无 error
- [x] 5.3 方案 step5b 条目勾选并附证据指针(wheel 构建、干净安装 job、探针双模式);SC manifest 不翻转(SC01/SC02 留 step5c),核对 `blocked_by` 注释仍准确。验证:git diff 仅该条目及括注
