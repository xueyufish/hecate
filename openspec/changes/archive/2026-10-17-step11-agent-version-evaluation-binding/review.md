# Codex 复核 — step11-agent-version-evaluation-binding

复核对象为 feat/step11-agent-version-evaluation-binding 的未提交实现。结论：下面的问题已直接修正，当前切片没有遗留阻断项；尚未提交、推送或运行 PR CI。详细命令与支持范围见 verification.md。

## 已修复发现

| 优先级 | 原问题及影响 | 修正 |
|---|---|---|
| P1 | Run 仅保存 agent_version，仍按可修改的 task.config.agent_id 判断身份。修改任务可把旧评测转给另一 Agent 的同号版本 | Run 自有 Agent、版本、配置哈希和快照；发布直接按这些事实与 workspace 匹配，不再 join 当前任务配置 |
| P1 | item.generated_answer 非空时跳过执行，却把 Run 标记为指定版本的已完成评测 | 绑定运行强制实际生成；空数据集及生成失败不能成为已完成版本证据；保留未绑定路径原行为 |
| P1 | 空 persona/model 回退草稿，工具从草稿名单开始过滤；技能忽略 pin，非 chat 模式被按 chat 处理 | 快照空值使用平台默认而非草稿；工具按快照名单加载；SkillLoader 使用冻结名单和 manifest；workflow/three_layer 复用既有版本化入口，workflow 传精确 pin |
| P1 | 只启用 drift 时缺候选仍可能放行；回归缺基线也可能通过；漂移没有当前内容哈希 | 调用方强制 version_binding；缺匹配基线/必要快照明确失败；漂移传共享 live_dataset_hash，评分算法本体不变 |
| P1 | 迁移接到旧 ancestor 产生双 head；删列 downgrade 会销毁已绑定证据 | 接到 7a6e5d4c3b20；有绑定证据时拒绝删列降级。已在独立临时 PG 数据库实测升级及两类降级 |
| P2 | 任务部分更新漏掉 agent_version，版本单字段更新失效；跨 workspace 只检查任务而未完整校验数据集/运行/引用 | 完整处理保留、修改和显式 null；绑定阶段及执行适配器校验租户和资源，不静默回退 |
| P2 | warn/force 响应无实际报告，bypass 审计异常被吞掉，可能先移动发布指针 | 返回 passed/bypassed_by_force；force 必须先成功写操作者及完整报告审计，失败不发布 |
| P2 | progressive load_skill 的 registry 仍按草稿技能名单和 live 内容加载 | registry 同时接收冻结名单和 manifest，L2 读取与快照目录保持一致；回归覆盖草稿取消绑定后的 pin 内容加载和排除新草稿技能 |

## 验证边界

本复核验证评测与 AgentVersion 的绑定切片，不表示 step11 全部完成。模型调用使用替身；外部 provider 质量、完整发布制品/签名、部署激活和知识库数据快照不在本次范围。技能治理/发现、未 pin 技能内容、Memory policy 和工具实现沿用既有 live 契约。
