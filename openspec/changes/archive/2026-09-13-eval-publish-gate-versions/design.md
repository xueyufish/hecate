# Design: eval-publish-gate-versions (7.3a + 7.3b)

## Context

7.3 留下的两个 follow-up 各有一道被记录在案的推迟理由：publish 阻断是"跨特性契约变更，应独立评审"；命名版本担心"快照 + 版本两套机制互相打架"。7.3c 落地后，两个理由的前置都已消失——豁免让 pass_rate 口径诚实，快照机制（`_snapshot_dataset` canonical JSON + sha256、drift 检测、豁免不参与 hash）经两轮迭代语义稳定。

代码锚点：

- publish 链路：`src/hecate/studio/workflows/service.py` `publish_version()` + `_build_evaluation_report()`（建议式报告已挂在 200 响应上，本 change 把"建议"升级为可配置的"否决"）。
- 评估台账：`EvaluationScoreModel` 每条分数带 `source`（`llm_judge` / `deterministic` / `human`），逐 item 逐 metric 落库——门禁重算确定性口径的数据基础。
- 聚合：`ops/evaluation/engine.py` `_build_summary`（`pass_rate` 要求 item 的**所有** score 达标，混入 LLM judge 即被污染——门禁不能直接读 summary.pass_rate）。
- 快照：`ops/evaluation/tasks/runner.py` `_snapshot_dataset`（显式字段清单 + canonical JSON + sha256，内容六字段投影）。
- 触发：`ops/api/evaluation_tasks.py` `trigger_workflow_evaluation`（成本护栏在此前置校验）。

## Goals / Non-Goals

**Goals:**

- 7.3a：可配置的 publish 门禁（warn / require），只被确定性信号触发；阻断契约显式、可绕过、可审计。
- 7.3b：一等命名 dataset 版本对象——冻结、不可变、可引用（run 绑定）、可恢复（checkout）、可对比（diff）；与既有快照机制共用同一序列化与 hash 实现。
- 组合闭环：`require_dataset_version` 信号让 publish 可以要求"验证 run 使用了命名版本"。
- 偿还 7.3c 的 known-bad 标记 UI 欠账，交付 7.3b 承诺的 checkout/diff UI 与门禁配置 UI。

**Non-Goals:**

- **env binding（环境→版本绑定表）不做**（Braintrust 的 staging/prod 绑定）：需要独立的权限与审计面，`require_dataset_version` 已承接最小组合闭环；等门禁真实使用反馈再立项。
- publish 时不强制新跑评估：门禁读最近 completed run；强制新跑会拖慢 publish 并新增成本失败模式。
- 版本对象不做"指针切换 / 分支"语义：版本永远只读，live dataset 永远是唯一可编辑面。
- 不做版本自动晋升（如"checkout 自动建版本"）：所有版本创建都是显式动作。
- 不动 online 侧台账、7.4 人评 reconciliation、`POST /runs/compare`（读 summary，自动继承）。

## Decisions

### D1. 门禁配置 = `WorkflowModel` 专列 JSON，三态模式 + 信号开关

`evaluation_gate` JSON 列，`NULL = off`。非空形状：`{"mode": "warn"|"require", "min_pass_rate": float|null, "block_on_regression": bool, "block_on_drift": bool, "require_run": bool, "require_dataset_version": bool}`。候选方案：全局 ops 设置（粒度太粗——不同工作流风险画像不同）、feature flag（运行时开关但表达不了 per-workflow 阈值）、独立配置表（一个 blob 不值得一张表）。更新走 workflow PATCH，服务端校验：`mode=require` 且六个信号全空/全 false 时拒绝（什么都不把关的门禁是摆设）；`min_pass_rate` 越界 [0,1] 拒绝。
**评估**：全局开关——省一列，牺牲 per-workflow 粒度；feature flag——切换瞬时，但阈值无处安放。

### D2. 确定性-only = 从 score 台账按 source 重算，不是 run 级约束

门禁读取该 run 的 `EvaluationScoreModel` 行，`source="deterministic"` 过滤后重算：(a) per-item 确定性通过 = item 的全部确定性分数 ≥ threshold（threshold 取 summary 里持久化的生效值），`deterministic_pass_rate` = 通过 item / 快照活跃 item；(b) per-metric 确定性均值用于回归比对。LLM judge 与 human 分数**永不参与阻断**（7.3 R2 原话的落地）。备选方案 (b)"要求 run 只配置确定性评估器否则门禁沉默"被否：用户配置了混合评估器后门禁静默失效是最危险的失败模式——静默比拒绝更糟。run 无任何确定性分数时，`require` 模式判为不满足（信号 `no_deterministic_scores`），`warn` 模式只报警。
**评估**：run 级约束——实现省一次查询，代价是混合配置下门禁静默失效。

### D3. 阻断契约 = 409 + 现有错误信封；force 走可选请求体 + 审计

被阻断的 publish 返回 `409 Conflict`，body 遵循仓库错误信封：`{"error": {"code": "EVALUATION_GATE_BLOCKED", "message": ..., "details": {"gate": <逐信号判定>, "evaluation_report": <完整报告>}}}`。选 409 不选 422：请求本身合法（版本存在、schema 正确），是资源当前评估状态与请求冲突；不选 200+`blocked:true`：既有客户端会把被阻断的发布当成功，违反"契约变更最小惊吓"。绕过：publish 请求体新增可选 `{"force": true}`（今天无 body，加性兼容），force 时照常 200、`evaluation_report.gate.bypassed_by_force: true`、审计日志记录 bypass 动作与操作者。`mode=off`（列 NULL）与 `mode=warn` 下响应与今天逐字节一致——warn 只在 report 里附加 gate 判定，不改状态码。
**评估**：422——语义"请求本身有问题"，不准；200+blocked——省一个状态码，毁掉 publish 成功语义。

### D4. 版本对象 = 持久化快照；单一序列化实现点

`EvaluationDatasetVersionModel(dataset_id, name, description, items, content_hash, created_by, ...)`，`(dataset_id, name)` 唯一（软删除不释放名字）。items 冻结与 content hash **复用 `_snapshot_dataset` 的同一投影与 canonical JSON 代码**（提为共享函数），不写第二份实现——7.3 担心的"两套机制打架"在实现层就此免疫。版本创建后不可变（无 update 端点），软删除允许且不影响既有 run（run 内嵌快照自足）。run 绑定只收 `dataset_version_id`（UUID），不收名字字符串——7.3 R7 已把"任意版本字符串进配置"标记为误用面。
**评估**：独立第二套序列化——实现快，hash 语义必然漂移；收名字字符串——CI 脚本友好，但名字冲突/重排的歧义由调用方买单。

### D5. checkout = 复制回 live dataset；live 永远是唯一可编辑面

`POST .../versions/{vid}/checkout`：把版本 items 复制回 live dataset——现 live items 全部软删，按版本 items 重新插入（新 item id；内容、tags、metadata、known_bad 标记及其 provenance 原样保留），响应携带 diff 摘要（added/removed/changed 计数）。否决的候选："指针切换"（dataset 语义复杂化，正是 7.3 担心的形态）、"checkout 生成新 dataset"（runs 引用旧 dataset_id，身份断裂）。checkout 是破坏性动作：文档明确"先对当前 live 建版本再 checkout"；UI 层做二次确认，API 层不加确认参数（保持 API 机械性）。
**评估**：指针切换——省一次复制，代价是"编辑到底改的是哪一层"永久含糊；新建 dataset——安全，但引用链全断。

### D6. diff 按 item id 对齐 + content-only hash 分类

`GET .../versions/{vid}/diff?against={version_id|"live"}`：按 item id 对齐，用与快照相同的内容六字段投影分类 `added / removed / changed`（changed 给字段级 delta：query、expected_answer、context、tags、metadata、known_bad）。vs live 时实时计算 live 投影。响应形状与 run compare 的 `dataset_drift` 风格保持一致，让前端可复用渲染。
**评估**：按位置对齐——实现省事，item 删插后 diff 全错。

### D7. 版本绑定 run：快照与执行同源（items override）

触发请求带 `dataset_version_id` 时：runner 跳过 live 抓取，直接以版本 items 充当 `dataset_snapshot`（原样拷贝版本 items + 版本 hash + 版本元信息），并把 items 显式传给 engine 执行（engine 新增可选 items override 参数）；成本护栏按版本 items 计数而非 live 计数（两者可能不同）。run 落 `dataset_version_id` 列。无绑定的 run 行为完全不变（live 抓取 + 完成时 drift 对比）。这使版本绑定 run 严格强于 live run——数据在执行前就已钉死。
**评估**：只绑快照不改执行——实现省一步，但"执行时 live 又变了"的窗口仍在，版本承诺落空。

### D8. 门禁判定收敛单点；report 是唯一读出面

门禁逻辑放 `ops/evaluation/publish_gate.py`（evaluation 域提供判定，studio 域组合调用），输入 run + baseline run + gate 配置，输出结构化 GateResult；`_build_evaluation_report` 把 GateResult 挂进 report.gate、把版本元信息挂进 report.dataset_version。compare / reports / CLI 不读 gate（gate 是 publish 域概念）。
**评估**：各处自行实现 gate 判定——口径漂移温床（7.3c D7 同一教训）。

### D9. summary 持久化生效 threshold

engine 在 `threshold` 配置存在时把它写进 summary（加性新键）。门禁 per-item 重算需要它，报告/CLI 的"这个 pass_rate 按什么算的"从此自描述。不改变任何既有 summary 键的语义。
**评估**：门禁自带 item_threshold——两处阈值来源，配置地狱；从 task.config 反查——join 链脆弱且 ad-hoc task 语义含糊。

### D10. UI 一次性偿还，API-first 姿势不变

三块：dataset 版本管理面板（列表/创建/checkout/diff 视图，复用 dataset_drift 渲染）、item 列表 known-bad 标记入口（7.3c 欠账：徽标 + 标记/解除动作 + reason 必填校验）、publish 流程 gate 呈现（配置入口、阻断态完整展示 report+gate、force 二次确认）。落点 `web/src/app/(dashboard)/ops-center/evaluation` 与 workflow 相关页面，apply 时按现有页面信息结构细化。
**评估**：UI 再推一批——欠账复利，7.3c 的豁免能力对非 API 用户等于不存在。

## Risks / Trade-offs

- **R1: 门禁读"最近 run"可能陈旧**（run 之后 dataset 改了）→ `block_on_drift` 信号可选开启；report 恒久暴露 run 的 `captured_at` 与 snapshot hash。不强制 publish 时新跑（成本与延迟不可接受）。
- **R2: 409 是 publish 的新失败模式** → 仅用户显式开启 require 后存在；warn 模式是官方灰度路径；错误体自带完整 report，客户端无需二次请求即可展示。
- **R3: `deterministic_pass_rate` 与 summary.pass_rate 并存的两套数字** → 命名明确区分；summary 落 threshold 让两套口径都可复算；文档写明各自语义（全评估器口径 vs 确定性口径）。
- **R4: checkout 破坏 live 编辑** → 响应带 diff 摘要可事后审计；文档 + UI 二次确认；不做 API 层确认参数。
- **R5: 门禁判定与 run 完成之间的竞态**（publish 读 run 时另一 publish 改 published_version）→ baseline 选择用"读时的 published_version"，判定是纯函数式快照读，无跨请求锁；最坏情形是 baseline 落后一次 publish，warn/require 都不会误放行已失败信号。
- **R6: 版本对象数量膨胀** → 软删除兜底；list 分页；v1 不设配额，观察真实使用。

## Migration Plan

- 单个 Alembic 迁移：建表 `evaluation_dataset_versions`；`evaluation_runs` 加 `dataset_version_id`（nullable + index）；`workflows` 加 `evaluation_gate`（JSON nullable）。全部加性，存量行零影响、无需 backfill。
- 回滚：downgrade 删表删列；版本 items 若有后续引用需先确认无活跃 run 依赖（run 内嵌快照自足，实际无硬依赖）。
- 部署无顺序要求：旧代码忽略新列/新表。

## Open Questions

（无阻塞项。UI 的具体信息结构在 apply 阶段按现有 evaluation 页面风格细化，不构成 spec 级不确定性。）
