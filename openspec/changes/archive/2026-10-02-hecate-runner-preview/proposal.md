# Proposal

## Why

step5b 已交付 `hecate-runtime` 内核 wheel（干净安装冒烟、可选能力启动声明、内核配置与平台 settings 解耦），但业务 App 仍无法独立消费执行能力：内核只是库，没有服务入口、本地定义加载、身份校验与本地证据。方案 step5c 要求交付独立执行宿主 `hecate-runner` 并以本地只读技术预览收口 **I-Ba（独立执行技术预览）迭代**；SC01（干净安装与无控制面冷启动）、SC02（结构化库存读取与越权调用）目前均为 `planned`，按方案验收"5c 无控制面冷启动并完成只读 SC 场景"在本步翻转为 `implemented`。这是业务 App 类消费者可开始集成的最早点。

## What Changes

- 新增 workspace 包 `packages/hecate-runner`（宿主），仅依赖 `hecate-runtime` 与既有内核依赖，提供 `hecate-runner` 控制台入口与 `python -m hecate_runner` 启动；**不依赖完整 `hecate` 主应用**。
- **profile 目录启动**：`agent-manifest.json`（step3 `ArtifactManifest` schema，摘要校验）、`runner.json`（端口、证据目录、模型配置、工具允许列表）、身份信任材料文件；secret 只以引用（env 名/文件路径）出现，不内联。
- **制品 manifest 加载移入内核**：`hecate_runtime.manifest`（自 `src/hecate/contracts/execution/manifest.py` 迁移，主应用保留单向 re-export 兼容）——manifest 是内核的输入格式，宿主不得为加载它而依赖主应用。
- **HTTP/JSON 服务入口复用 step3 绑定**：`/capabilities`、`POST /runs`、`GET /runs/{ref}`、`/events`（游标）、`/cancel`、`/artifacts` 按 `execution-backend.http.v0_1.yaml` 暴露；宿主扩展 `/healthz`、`/v1/evidence` 查询与关停端点。错误用契约的 problem+json 语义。
- **服务端身份校验**：请求凭证对本地信任材料验证，principal、角色（read_only）、数据域作用域**全部由服务端映射解析**；请求体自报角色被忽略；未认证请求拒绝且留证据。
- **只读技术预览档**：manifest 声明 write/approval_required 工具时启动失败；仅允许列表内的 read 工具执行；工具参数 schema 校验先于分发；后台自动重试、长任务恢复、持久任务按能力声明为 `unsupported`（复用 `capabilities_status` 机制），不以缺省静默表达。
- **确定性 CI 模型 + 可配置模型 endpoint**：默认 stub 模型（CI 确定性）；配置 endpoint 时经 httpx 调用；证据记录模型来源；真实 endpoint 运行不宣称供应商已认证。
- **本地证据**：执行与拒绝记录追加写入本地 append-only 证据存储（含 outcome 分类），本地接口可查询；默认不向任何中心上传。
- **业务 API 工具适配**：`query_inventory` 只读工具按服务端已验证 principal 调用业务 App 的 HTTP API（fixture 为 `StubInventoryApi`）；业务规则（域隔离、角色、审批）留在业务 API 侧。
- **SC01/SC02 翻转**：新增 `tests/scenarios/test_sc01_cold_start.py`、`test_sc02_inventory_read.py`（干净 venv 装 wheel、无仓库源码路径、无管理平台地址，经 HTTP 驱动宿主断言），manifest 条目翻转为 `implemented`；独立消费基线 §5 登记行按一致性义务同步（带日期注记，保持快照性质）。
- CI 扩展：wheel 安装 job 增加 runner wheel 构建安装与 SC 场景执行。
- 不修改平台主应用行为；技术预览不承诺持久任务恢复、生产写入、即时远程撤权或高可用（step6/7、最小 step10/11 与 step16 的门）。

## Capabilities

### New Capabilities

- `standalone-runner-host`:独立执行宿主（hecate-runner）的行为契约——无控制面冷启动、服务端身份校验、manifest 门禁的只读预览档、本地证据与默认零外发、确定性 CI 模型与可配置 endpoint、健康与关停语义。

### Modified Capabilities

（无——SC01/SC02 的翻转语义已由 `platform-scenario-pack` 既有 requirement 覆盖，翻转本身是清单数据变更；HTTP 绑定与身份传递语义已由 `execution-backend-contract` 固定，宿主是它的实现侧消费者，不修改其需求。）

## Impact

- **新增**:`packages/hecate-runner/`（源码、pyproject、README、profile 样例）、`tests/scenarios/test_sc01_cold_start.py`、`test_sc02_inventory_read.py`、`tests/scenarios/tools/runner_harness.py`（干净 venv 安装/启动/驱动宿主的共享 harness）。
- **修改**:`packages/hecate-runtime`（新增 `manifest` 模块）、`src/hecate/contracts/execution/manifest.py`（改 re-export）、`tests/scenarios/manifest.yaml`（SC01/SC02 → implemented）、`docs/refactor/standalone-consumption-baseline.md`（§5 两行登记同步）、`.github/workflows/ci.yml`（runner wheel + SC 场景）。
- **CI**:SC 场景测试自带临时 venv（`uv`），主测试 job 需可用的 `uv`；无网络外部依赖（stub 模型、本地业务 API stub）。
- **不受影响**:平台主应用执行链、API 契约、数据库 schema、既有 spec 行为。
