# Lightweight Change

## Risk classification

本次仅校准演进方案对既有主线实现的描述，属于 AGENTS.md 的 docs-only lightweight。沿用 ADR-037 的目标职责和现有退出门槛，不新增产品要求、接口、运行语义或架构决策。

## Existing behavior or contract

演进方案须分开记录规划、技术切片交付与生产认证；保留 step1—step19、Feature ID、历史证据和未完成门槛。核对前已成功执行 git fetch origin main，基点为 40f7e129b7c296be2baf4a6ef3f086394eb1c7aa。原方案最后修订为 1017abdf，后续主线已有 step2/3/6/7/11/14 交付，部分待办与当前代码不符。

## Scope

- 逐项检查 step1—step19 的实现、测试和归档证据，更新状态与剩余责任。
- 重点修正规范同步、ExecutionDriver、单回合决策/动作恢复、共享工具执行、版本绑定评测与发布回执、只读任务观察的过期描述。
- 同步本文件内依赖表、近期路径及迭代说明，消除外部 Runtime 已撤销与后文仍承诺接入的矛盾。
- 在 verification.md 保留基点、逐步骤核对和本次实际验证结果。

## Acceptance criteria

- step1—step19 各有可核查结论；已交付项不再作为全新待建任务。
- AgentTurn 单回合不等于完整多轮 Controller；Driver 解耦不等于入口切流；发布凭据不等于完整 release manifest；只读观察不等于干预闭环。
- step8 保持撤销；G2/G3/G4、真实模型 gate=false、独立 Runner 与生产认证等未闭合项保持显式。
- 文档内本地证据链接、步骤编号、Markdown 基本结构、OpenSpec strict、规划数据漂移及适用共享检查通过；运行支撑本次状态判断的定向回归，明确未重跑的 PG/真实模型/前端认证。

## Non-goals

不改代码、主 specs、ADR、功能清单、路线图、CI 或发布门禁，不切换默认入口，不重新授予生产支持。不提交或推送，不归档本次 change。
