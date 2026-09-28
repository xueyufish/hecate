# Design

## 背景与范围决策

本 change 是 step1 的收尾:四个已合并 change 完成了 step1 的主体,本 change 只补齐对照操作清单后仍缺失的项并同步状态标记。所有决策围绕一个原则:**不虚报完成状态**——能以代码证据关闭的项关闭;受外部门槛约束的项显式登记门槛,保留未勾选。

### D1:单 change 承载补齐与簿记

补齐项(§六复核、TODO-O1、S11、成本口径)与簿记项(勾选同步、归档任务关闭、manifest flag)同属一个目的:关闭 step1 退出条件。拆开会产生引用悬空(勾选引用尚未合并的补齐内容)。PR 内以独立 commits 保持可回滚粒度。

### D2:基线文档扩展的合法性

基线文档头注声明"不跟随后续提交更新"——该约束针对 step2+ 的 change(状态源是 feature-inventory 与路线图)。本 change 本身是 step1 原始范围的未完成部分,扩展 §6、新增 §11 属于完成 step1 交付,不是状态追踪。§7 仅修正已过时的事实(G2"待合并"→已合并 #186),不新增追踪职责。

### D3:勾选策略

方案 step1 勾选框逐项判定:

- 实质完成且证据在本仓库可复核 → 勾选,附证据指针。
- 交付物存在但字面要求含外部门槛 → 保留未勾选,附注门槛与目标步骤(清单项 7:实际组合数据流随 step4 Deployment 模型;清单项 13:真实成本基线受 G4 门槛、多 Agent 比较随 step12/13 团队模型)。
- owner 字段:方案 §七规定人员由用户安排,基线 §7 保留待指定;清单项 3 勾选并附注该前提。

### D4:TODO-O1 方法与边界

方法:对非 studio 域(ops/tools/channel/enterprise)grep studio 拥有的 ORM 模型导入面,再对命中文件核对写操作(`db.add`/`flush`/`commit`)。记录**已证实样本**(file:line),不宣称全量覆盖;运行时使用量与动态路径标注【未核验】。共享事务面与全局可变状态(请求级 AsyncSession 跨域共用、模块级单例)单独登记。

### D5:§六边界复核的分类维度

每个能力记录四个正交事实,不以单一标签混淆:

1. **打包状态**:完全耦合在主应用(src/hecate 内模块)/ 已拆包但仍为核心依赖(在 `[project].dependencies`)/ 已拆包、可选安装(仅 lazy 挂载,不在基础依赖,CI 经 `uv sync --package` 显式安装)。
2. **启用方式**:无条件挂载 / 条件挂载(开关或 try-import)/ 后台 wiring。
3. **调用面证据**:路由、import 方向(代码可核验);运行时使用量【未核验】(无生产部署数据)。
4. **owner**:待用户指定(方案 §七)。

### D6:S11 独立复核的设计

- **独立性定义(确定性切片)**:复核者是独立的 AgentModel 行与独立会话,其输入只含草稿文本与复核指令(不含起草会话上下文);更深的内容质量复核属 Tier-2/step10 rubric 范围,manifest 中显式声明。
- **链路**:语料读取走真实 ToolRegistry 边界的 stub 工具(与工单 stub 同模式);审批走 S03 已验证的 ask 规则 + `_inner_backend` 授权 seam;工单写入断言恰一次。
- **脚本路由**:两个 HTTP 会话共享模块级 stub LLM;responder 以消息内容特征(复核指令标记、工具结果字段)确定性地判定所处阶段,不依赖调用顺序。
- **否决变体**:复核未通过时不提议写工具 → 零副作用、零审批事件,证明复核先于审批门禁生效。

### D7:成本基线口径

G4 未关闭(统一常量单价、估算来源不完整),现在无法产出真实成本基线;伪造数据违反方案"不得标为真实成本闭环"。因此只固定**采集口径**:Tier-2 记录的 `_meta.cost_baseline` 块声明采集状态(`not-collected`)、原因、门禁(G4 → step7/step10)与采集时的字段清单(token 分类、reported/estimated 来源、价格版本、延迟)。确定性 rubric 运行无模型调用,`not-collected` 是事实而非缺陷;以真实模型运行时按该 schema 采集并区分 reported/estimated。

## 落点

- `docs/research/platform-evolution-baseline.md`:§6 扩展、§7 事实修正、§8 引用声明、§9 登记、新增 §11。
- `tests/scenarios/manifest.yaml`、`tests/scenarios/test_s11_independent_review.py`、`tests/scenarios/baseline/run_baseline.py`、`tests/scenarios/baselines/single_agent_baseline.json`。
- `openspec/changes/archive/2026-09-28-g1-mcp-action-enforcement/tasks.md`:5.5/7.1/7.4 关闭注记。
- `docs/research/enterprise-agent-platform-evolution-plan.md`:step1 勾选同步。

## 风险与回退

- 纯文档 + 测试资产,不改产品行为;回退 = revert 对应 commits。
- manifest 翻转 `synced` 后受 `test_baseline_doc_sync_obligation` 钉住:基线文档必须包含 `tests/scenarios/manifest.yaml` 字符串,漂移即测试失败。
- S11 与既有场景共享 conftest fixture,不修改既有 fixture 语义;既有 S01—S10 回归必须全绿。
