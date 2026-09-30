# step2 复核与修正记录

## 复核范围与证据

本次基于 `32e6fdc`（PR #195）复核。原 change 已归档，修正由 [step2-review-hardening](../../openspec/changes/step2-review-hardening/proposal.md) 承接。依据为[演进方案](enterprise-agent-platform-evolution-plan.md) step2/§六、[归档规格](../../openspec/changes/archive/2026-09-29-governance-platform-positioning/specs/feature-inventory-governance/spec.md)、实际清单脚本、CI 和文档。

证据类型是静态代码/安装声明核对、清单工具回归和文档一致性。清单 `evidence` 中的本报告引用只证明规划/静态核对；`acceptance` 记录后续实现应满足的行为。新增条目保持 planned，未确认的成熟度为 unverified。原有 delivered 事实保留；[SC manifest](../../tests/scenarios/manifest.yaml) 与[独立基线](standalone-consumption-baseline.md)中的目标能力仍待实施。实际业务使用量、收益、资源下限与生产支持没有新的证明。

## 发现与处置

| 发现 | 证据 | 修正 |
|---|---|---|
| 增量严格校验缺失 | `--strict` 只令全部历史 warnings 失败，CI 非严格；新增项无验收仍能进入 | `--base-ref` 比较 Git 条目语义，严格准入新增/变更，保留未变历史欠账；CI 按事件提供基线，基线错误失败 |
| 研究晋级证据可绕过 | YAML 改 delivered、catalog 同时加 ✅，无 evidence/acceptance 仍退出成功 | 使用基线原状态或生命周期字段检查晋级；逐项报告研究欠账，空白/空集合及生产占位值不算证据 |
| 漂移只报告首行 | 无 ID+字段级完整差异，删 marker 可跳过 | 报告所有变更字段/增删行/正文，正式区域完整且 strict=true；非法注册/行宽写入前拒绝 |
| 里程碑列错位 | 标题与数据列宽不同 | 补 Scope，结构校验防止同类错误 |
| §六映射缺失 | Team、跨 Runtime 协作、trace、数据治理、客户端、Sandbox 等无决策 | [受管逐行映射](../features/feature-catalog.md#plan-disposition-mapping)与 plan YAML；保留 ID 并补独立验收缺口 |
| 处置表未含实际分类/提供方 | 缺 disposition/provider，已交付内置能力被标未来 external-service | 添加处置字段与当前实现/提供方，目标边界、退出条件和 profile 状态另列 |
| 契约 owner/发行单元模糊 | 缺 owner 列，以源码目录代发行单元 | 明确责任域、schema/包/adapter 发行单元、状态 owner、未确定的版本和支持窗口 |
| 口径与既定方向矛盾 | positioning under review、所有能力 MCP、已统一 ExecutionRequest、源码箭头反向 | Accepted target、当前与目标分开，RuntimePort 名称/方向修正，补断连撤权陈旧窗口 |
| 历史排期冒充当前 | 当前统计过期、固定月份和旧 Sprint 混有待交付能力 | 历史快照/排期显式标注；未来顺序由 I/M 表定义，状态以 inventory 为准 |

## 已实现候选决策

重新核对 [pyproject](../../pyproject.toml)、[main 挂载](../../src/hecate/main.py)、[原基线 §11](platform-evolution-baseline.md#11-已实现能力边界复核方案-六对照)和[独立闭包](standalone-consumption-baseline.md)。以下代码/数据责任域是领域 owner；具名实现与验收负责人依用户既定安排，在所属 change 启动时分别指定。实际使用量均未验证，不据此删除能力。

| 候选 / Feature | 代码与数据责任域 | 当前安装/挂载和调用证据 | 目标/替代接口 | 兼容、迁移和回退窗口 | 处置 |
|---|---|---|---|---|---|
| Pregel / 1.3.1 等 | runtime 执行事实/checkpoint；平台任务责任/投影 | [runtime](../../src/hecate/runtime/pregel.py)在主应用，经执行服务装配，无独立 wheel | hecate-runtime/runner + AgentExecutionBackend；step5/8 | 增量抽取、旧导入薄兼容；旧路径保留至调用方迁移、step19 证明无调用。新 Run 切流，活跃绑定固定；不兼容状态拒绝回退 | builtin-reference |
| Memory/RAG / 4.x、3.1.x—3.2.x | 各 provider 数据/索引；平台绑定授权 | [hecate-memory](../../packages/hecate-memory/README.md)可选，main try-import 挂载；实际启用未知 | Memory 与 Knowledge binding、整体/组件独立认证；step9 | 保留旧 API/索引绑定到 ACL、删除、导出迁移通过；step19 清依赖。step9 指定双版本窗口，不设在线双真源 | optional-component |
| 内置评测 / 7.2b—7.4 | ops/evaluation 任务/结果；Governance 发布 | [评估引擎](../../src/hecate/ops/evaluation/engine.py)在主应用、评估路由直接挂载，hecate-ops 默认依赖 | EvaluationBackend 与可选 builtin evaluator；step10 | 旧结果/数据集/evaluator 版本可读，核对结果后只切新评测；旧读接口到迁移/支持窗口结束，回退不追授已拒绝发布 | optional-component |
| 微调/Hub / 6.6、6.44—6.47 | hecate-llm Hub 数据；平台准入/凭据/预算 | hecate-llm 必需依赖；Hub try-import 仅挂载保护，启用率未知 | 可选 Model Hub/外部 adapter；step7/19 | 保留管理 API/配置引用，核对消费者、数据迁移和无 Hub 执行；旧 adapter 到消费者升级和对账完成后退出，回退不恢复撤销权限 | optional-component |
| Prompt/Skill 学习 / 6.19、1.3.6f | 优化/演化候选；prompt-owning 服务版本；Governance 发布 | [优化](../../src/hecate/ops/prompt_optimization/service.py)/[演化](../../src/hecate/studio/self_evolution/pipeline.py)主应用路由，跨域写入待迁移 | 可选优化服务，候选→评测→门禁；step11/15/19 | 保留候选/结果/版本，先统一发布门禁再迁移表写入；旧读取到历史制品可查和调用方迁移后退出，回退不跳过审批 | optional-component |
| 编辑器/DSL / 1.1.2、1.1.3 | studio 定义/草稿；runtime 编译；Governance 发布 | [DSL](../../src/hecate/studio/workflows/graph_dsl.py)/[画布](../../web/src/components/workflow/canvas-area.tsx)耦合主产品 | 内置 Engineering；外部 Agent 免 DSL 登记；step11/18 | 旧定义可编辑/运行，增加中立 manifest；载入器至定义迁移/兼容窗口结束；回退只改新任务路由 | builtin-reference |
| 执行工具 / 5.1、6.27 | tools 动作；环境 provider 资源 | [工具](../../src/hecate/tools/tool/registry.py)在主应用，sandbox 默认依赖，搜索 extra；启用量未知 | 可选工具/environment，统一 gateway；step7/18/19 | 先验证动作分类/回执/身份/隔离再分依赖；旧受控 adapter 保留至迁移完成，回退不得恢复危险旁路 | optional-component |
| 插件安装器 / 5.5b | 安装状态；目录/准入逐步归位 | [安装器](../../src/hecate/core/plugin/installer.py)及 plugins router 主应用；安装量未知 | 中立 manifest、外部仓库/扫描/安装服务；step18/19 | 固定摘要/许可/权限/可撤销绑定，双版本至迁移完成；回退不信任已撤销包、不执行安装脚本 | builtin-reference |
| IM / 11.9 | channel 协议映射；平台任务/授权 | [渠道包](../../packages/channels/hecate-channel-slack/README.md)可选；主应用消息总线/注册路径；消息量未知 | 可选 channel adapter；step5/19 | 逐入口迁移，活跃 Run 固定路由；旧协议到兼容测试与客户切换完成，回退保留服务端身份/资源授权 | optional-component |
| SIEM / 8.7 | ops/evidence 事实；导出 adapter | [导出器](../../src/hecate/ops/siem/exporter.py)在主应用经后台 wiring；目标/量未知 | 中立非采样 evidence envelope 与外部 SIEM；step10/19 | 分离本地保存/上传，去重补传；旧 exporter 到目标迁移/保留/对账完成，回退不重放业务动作 | integration |

回退窗口由退出条件限定。任何旧 adapter/接口移除前，所属 change 必须登记具体支持版本、调用方迁移、数据兼容和回退演练；当前没有候选被判为 retire-candidate。

## step2 完整性核对

| step2 要求 | 交付 / 结论 |
|---|---|
| 定位、交付边界、模式、业务 App 契约 | positioning：Accepted target、HTTP/JSON、独立/受管/完整平台、网络分轴及预览/生产门槛 |
| 托管后端/信任、域/数据 owner 与例外 | architecture、ADR-034：harness/环境/执行点、字段单主、例外责任域，断连撤权期限；实际实现由所属 step 完成 |
| 包迁移决策 | ADR-035 与 INDEX：包名保留，依赖方向修正；不预建发行包 |
| §六处置、ID 与 P/SC 映射 | catalog/inventory/plan YAML：逐行覆盖、无旧 ID 删除，契约缺口独立验收、Memory/Knowledge 分离，图能力可选 |
| G5/schema/字段所有权与受管生成 | 保留既有任意扩展字段；新/变更严格、历史欠账可见；结构/区域/逐字段漂移与 sync 幂等 |
| research/Labs 生命周期 | pending 指派显式；缺字段逐项报告，晋级需结果和验收 |
| 身份/策略/调度/审批/发布/证据/资产 | core 映射 provider/执行点/状态 owner/验收具备；实现可由企业服务提供 |
| 能力契约/独立发布/支持窗口 | Registry 有责任域、schema/包/adapter、状态 owner；独立契约未认证窗口显式标注 |
| 能力域与 roadmap | 受管域表与 architecture 对应；I/M 为当前顺序，历史统计与 Sprint 标为历史 |
| 完成证据范围 | 规划与工具交付；目标宿主、真实后端、生产 profile 仍须后续运行测试 |

## 验证记录

本次修正以原归档后的 Git HEAD 为基线，比较原有 ID、交付状态、成熟度、依赖、既有证据/验收文本及扩展字段；原有 ID 和 delivered 事实均保留，新建独立缺口均为 planned/unverified。实际 §六受管映射逐行覆盖原候选、平台核心、后端、延期、独立消费与实验责任；连续两次 extract/sync 后，inventory 与两份受管文档字节未变。

- 清单工具受影响测试通过。包含新增/变更准入、研究晋级、生产证据占位、同时删除两处旧 ID、受管区域删除/降级、全部字段差异及生成前结构拒绝。
- `check --base-ref HEAD`：通过；所有相对原基线新增或变更的条目通过准入，错误为零，历史欠账继续产生告警。部分旧条目仍缺 evidence 或 acceptance；这些是历史欠账，增量门禁不将其误报为新能力已验收。
- 受管生成：`sync --check` 通过；表头、分隔行和行宽校验通过。PR、push、merge_group 的 CI 基线表达式、完整 Git 历史、Bash 语法及缺失/全零基线分支均经本地模拟核对；真实 GitHub CI 尚未运行。
- 质量检查：ruff check 与 format --check 覆盖 CI 的 `src/hecate/ packages/ tests/ scripts/feature_inventory.py`；mypy `src/`、OpenSpec strict validate、`git diff --check` 全部通过。本机为 Python 3.14；仓库 CI 的 Python 3.12 环境仍由 PR 验证。
- 关联文档的本地 Markdown 链接目标检查通过；定位、架构、ADR、catalog、roadmap 与方案引用可解析到现有文件。

后续实施 step 仍须逐条关闭历史欠账并提供真实宿主、后端、供应商、网络与生产 profile 的行为证据。此处的规划记录及静态证据不授予任何生产认证。
