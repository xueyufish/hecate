# Tasks

任务组 1—2 为内核/包基座,3—5 为宿主能力,6—7 为 SC 场景与收尾。每组完成即勾选。

## 1. 内核 manifest 模块迁移

- [x] 1.1 将 `src/hecate/contracts/execution/manifest.py` 迁移为 `packages/hecate-runtime/src/hecate_runtime/manifest.py`(逻辑不变),主应用原路径改为兼容 re-export;验证:全仓 grep 无深路径残留引用,`pytest tests/test_runtime/ tests/test_contracts/ -q` 相关用例全绿(如有 manifest 既有测试随迁至 runtime 包测试)
- [x] 1.2 在 hecate-runtime 包内补 manifest 校验测试(摘要不匹配、越权 permission、缺 entry 的失败路径);验证:`pytest packages/hecate-runtime/tests -q` 通过

## 2. hecate-runner 包基座

- [x] 2.1 新建 `packages/hecate-runner`(pyproject 加入 workspace、console script `hecate-runner`、依赖仅 hecate-runtime);验证:`uv build --package hecate-runner` 产出 wheel,`pip show` 闭包无完整 `hecate`
- [x] 2.2 实现 profile 加载:manifest 摘要校验(复用 1.1)、`runner.json` 解析、secret 引用解析(env/文件,不内联)、`identity.json` 信任材料加载;缺项/摘要不符/引用不可解析 fail-fast 且错误指明缺失项;验证:包内单测覆盖三类失败路径
- [x] 2.3 预览档门禁:manifest 含 write/approval_required 工具时启动失败指明工具名;allowlist ⊄ manifest read 工具时失败;后台重试/长任务恢复/持久任务在 `/capabilities` 声明 `unsupported`;验证:单测断言启动失败信息与能力声明内容

## 3. HTTP 服务与身份

- [x] 3.1 实现 stdlib HTTP 服务(ThreadingHTTPServer + 后台 asyncio 循环):`/healthz`、`/capabilities`、`POST /runs`、`GET /runs/{ref}`、`/events`(游标)、`/cancel`、`/artifacts`、`/v1/evidence`、`/admin/shutdown`;错误 problem+json;默认绑定 127.0.0.1、请求体上限;验证:包内单测覆盖每个端点的成功与错误形状
- [x] 3.2 服务端身份校验:常量时间凭证比较、principal/角色/域服务端解析、自报字段忽略、未知凭证拒绝并留证据;验证:单测含伪造角色、未知凭证、无凭证三负例
- [x] 3.3 工具参数 schema 校验先于分发;校验失败返回明确错误且业务 API 零调用;验证:单测断言 stub 业务 API 调用计数为 0

## 4. 执行装配、模型与证据

- [x] 4.1 启动装配:按 manifest entry + allowlist 编译最小固定图(模型 worker + read 工具 worker)、`configure_kernel` 安装 RuntimeConfig、InMemoryCheckpointStore、并发=1 队列拒绝语义;验证:包内端到端单测(提交→执行→事件游标→结果)
- [x] 4.2 默认确定性 stub 模型 + 可配置 endpoint(httpx,认证材料走 secret 引用);证据记录模型来源(stub|endpoint),无认证性表述;验证:两种模式的单测断言证据字段
- [x] 4.3 本地 append-only JSONL 证据(按天滚动,execution/denial 两类,outcome 分类);`/v1/evidence` 按 outcome/principal 过滤;证据写入失败 fail-closed 拒新执行;验证:单测覆盖追加、查询与写入失败路径

## 5. 业务 API 工具适配

- [x] 5.1 实现 `query_inventory` read 工具:以服务端解析的 principal/域构造调用上下文经 httpx 调业务 API;响应按契约错误分类透出;验证:单测用本地 stub HTTP 业务 API 断言调用与错误映射

## 6. SC 场景翻转

- [x] 6.1 实现 `tests/scenarios/tools/runner_harness.py`:构建双 wheel → 临时 venv 安装 → 写 profile(含 write-manifest 变体)→ 启动 stub 业务 API(StubInventoryApi HTTP 包装)与 runner 子进程(临时 cwd,无仓库路径);启动轮询超时上限与路径规范化集中于 harness;验证:harness 冒烟(健康端点可达)
- [x] 6.2 `test_sc01_cold_start.py`:无控制面配置冷启动、健康、只读执行完成、结果引用、证据可查、`/capabilities` 含 unsupported 项;验证:pytest 通过且 uv 缺失时 skip 带原因(CI 权威)
- [x] 6.3 `test_sc02_inventory_read.py`:授权域读取、跨域拒绝、自报角色忽略、未知凭证拒绝+证据、无效参数先于分发、write manifest 启动失败;验证:pytest 通过
- [x] 6.4 manifest 翻转:SC01/SC02 → `implemented`、删除 `blocked_by`;独立基线 §5 两行改带日期"预览已交付(5c)"并追加更新说明;验证:`pytest tests/scenarios/test_manifest_consistency.py -q` 全绿(含 SC 双向绑定与登记对齐)

## 7. CI 与收尾

- [x] 7.1 CI:wheel job 增加 runner wheel 构建安装与"无完整 hecate 泄漏"断言;主测试 job 补 `uv` 可用并确保 SC 两文件被收集执行;验证:workflow 语法检查 + 本地 `pytest tests/scenarios/ -q` 全绿
- [x] 7.2 `packages/hecate-runner/README.md`:profile 布局、启动命令、预览档限制(串行、无持久化、仅 read 工具、不建议公网)、SC 场景运行方式;验证:文档命令与实际入口一致
- [x] 7.3 本地四项验证:`ruff check src/hecate/ tests/ packages/hecate-runner/`、`ruff format --check`、`python -m mypy src/`(runner 包若有 mypy 配置纳入)、`python -m pytest tests/scenarios/ packages/hecate-runner/tests packages/hecate-runtime/tests -q`;验证:0 错误
- [x] 7.4 `openspec validate hecate-runner-preview --strict` 通过;方案 step5 清单在 PR 描述中对照(5c 技术预览切片完成,step5 整体待 5d);验证:命令输出无 error
