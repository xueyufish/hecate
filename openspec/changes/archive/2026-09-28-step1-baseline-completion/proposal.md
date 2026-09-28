# Proposal

## Why

演进方案 step1 的四个 change(`platform-evolution-baseline` #184、`g1-mcp-action-enforcement` #185、`g2-tool-receipt-recovery` #186、`platform-evolution-scenario-pack` #187)已合并,但对照 step1 操作清单复核,退出条件尚未全部满足:

1. **清单项 8(方案 §六逐能力边界复核)未交付**:未按"已是可选包 / 已拆包但仍为核心依赖 / 完全耦合在主应用"逐能力分类,未核对安装依赖与启用方式。
2. **TODO-O1 未关闭**:基线 §6 只登记了跨域**读**样本,跨域**写**路径与共享事务/全局状态未系统核验。
3. **清单项 10 缺"独立复核"环节**:验收样例链"读取材料 → 产生摘要 → 独立复核 → 人工批准 → 写入工单"中,独立复核无场景覆盖。
4. **清单项 13 成本基线缺失**:Tier-2 记录只有结果指标(citation/ACL),无成本字段口径;受 G4 门槛约束,需先固定采集口径并显式登记门禁。
5. **簿记漂移**:方案文档 step1 的 16 个勾选框只有 1 个勾选;G1 归档 change 有 3 个任务未用最终验证证据关闭(其中 tmp_path 阻断已在当前环境复核不复现);manifest 的 `baseline_doc_sync` 仍为 `pending` 而基线文档已存在。

本 change 是 step1 的收尾 change:补齐上述缺口并同步状态标记,不引入新的平台行为。

## What Changes

- **基线文档扩展**(`docs/research/platform-evolution-baseline.md`):
  - §6 增补跨域**写**路径已证实样本(MCP 写 AgentModel/KB 表、prompt_optimization 写 PromptVersionModel)、共享事务面与全局可变状态登记,关闭 TODO-O1。
  - 新增 §11"已实现能力边界复核":对方案 §六清单逐项给出打包分类、依赖证据(pyproject/CI 安装命令)、挂载/启用证据、调用面证据与负责人栏;运行时使用量数据显式标注【未核验】。
  - §7 G2 行更新为已合并事实;§8 增加对场景包 manifest 的引用声明;§9 登记 S11 与成本基线口径。
- **场景包扩展**(`tests/scenarios/`):
  - 新增 S11 独立复核全链场景(Tier 1 确定性):语料读取工具 → 起草 Agent 摘要 → 独立复核 Agent 审阅草稿 → 审批门禁 → 测试工单写入恰一次;复核否决变体断言零副作用、零审批事件。
  - `manifest.yaml` 登记 S11,更新 P06/P07 覆盖理由,`baseline_doc_sync` 翻转为 `synced`。
  - Tier-2 记录 schema 增加成本基线字段块(采集状态、未采集原因、门禁指向 G4、采集时字段清单);重生成 `single_agent_baseline.json`。
- **状态同步**:
  - G1 归档 change 的 tasks 5.5/7.1/7.4 以复核证据(2026-09-28 于 main 重跑:41 passed、1 skipped 为 Windows 主机 symlink 限制;CI 合并运行)关闭。
  - 方案文档 step1 勾选框逐项同步;受外部门槛约束的项(清单项 7、13)保留未勾选并附注门槛。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-scenario-pack`:新增独立复核全链场景要求与 Tier-2 成本基线字段要求。

## Impact

- **生产源码**:无改动(仅 `tests/scenarios/`、`docs/research/`、`openspec/` 归档任务注记、方案文档勾选)。
- **CI**:新增 S11 随 Tier 1 运行,无外部依赖;manifest 一致性测试自动覆盖新条目。
- **文档关联**:基线文档 §8 与 manifest 的引用关系由既有 `test_baseline_doc_sync_obligation` 测试钉住。
- **不受影响**:API 契约、数据库 schema、G1—G5 门槛的实质范围(G4 的真实成本闭环仍归 step7/step10)。
