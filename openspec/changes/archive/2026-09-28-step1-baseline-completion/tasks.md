# Tasks

## 1. 基线文档补齐

- [x] 1.1 §6 增补跨域写路径已证实样本(tools/mcp 写 AgentModel/KB 表、ops/prompt_optimization 写 PromptVersionModel)、跨域只读样本归属修正、共享事务面与全局可变状态登记;TODO-O1 关闭或转为显式剩余范围。
- [x] 1.2 新增 §11 已实现能力边界复核:方案 §六 十项能力逐项给出打包分类、依赖证据(pyproject dependencies、CI uv sync --package)、挂载/启用证据、调用面证据;运行时使用量标注【未核验】;owner 待指定。
- [x] 1.3 §7 G2 行更新为已合并事实(#186);§8 增加对 `tests/scenarios/manifest.yaml` 的引用声明;§9 登记 S11 与成本基线口径。

## 2. 场景包扩展

- [x] 2.1 新增 S11 独立复核全链场景(`tests/scenarios/test_s11_independent_review.py`):语料读取 stub 工具 + 起草 Agent + 独立复核 Agent + ask 规则审批 + 工单恰一次;复核否决变体零副作用零审批事件。
- [x] 2.2 `manifest.yaml` 登记 S11(implemented、tier 1、P06/P07),更新 P06/P07 覆盖理由,`baseline_doc_sync` 翻转为 `synced`;manifest 一致性测试全绿。
- [x] 2.3 `run_baseline.py` 增加 `_meta.cost_baseline` 采集口径块(状态、原因、门禁 G4、字段清单);重生成 `single_agent_baseline.json`,指标数值不变。

## 3. 状态同步

- [x] 3.1 G1 归档 change tasks 5.5/7.1/7.4 以复核证据关闭:2026-09-28 于 main 重跑完整 MCP 套件 41 passed、1 skipped(Windows 主机不允许创建 symlink;CI 合并运行覆盖 symlink 负例);7.4 推送确认已随 #185 合并完成。
- [x] 3.2 方案文档 step1 勾选框逐项同步:实质完成项勾选并附证据指针;清单项 7(托管组合数据流随 step4)与清单项 13(成本基线受 G4、多 Agent 比较随 step12/13)保留未勾选并附注门槛。
- [x] 3.3 owner 指派:用户决定暂缓(2026-09-28);基线 §7 注记、§11 引言与方案勾选注同步该决定,对应 change(step2/step5/step7)启动时再指定。

## 4. 验证与收尾

- [x] 4.1 运行 `tests/scenarios/` 全部场景、manifest 一致性测试、G1/G2 相关回归;全绿。
- [x] 4.2 `ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/` 通过;mypy 范围不含 tests(仓库既有口径)。
- [x] 4.3 `openspec validate step1-baseline-completion` 通过。
- [x] 4.4 分支确认:`feat/step1-baseline-completion`(已核实)。推送批准属聊天门禁,由用户在推送时行使,不随本清单闭合;归档顺序经用户选择为先归档、后推送。
