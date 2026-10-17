# 复核记录 — step6-accepted-agent-decision

## 结论

已对照基线 `0ad17a60`、演进方案、proposal/design/delta specs 和实际实现完成复核，下列问题已在用户指定 worktree 修正。当前单决策步骤范围内通过本地验证，尚未提交、推送或归档。

## 发现并修正

1. **[P1] 初稿 fencing 没有权威租约。** SQL 仅在唯一键冲突后比较决策行 token，新步骤首次提交可携带不存在的租约和任意 token 成功；同内容重投还可省略 ownership 绕过。已复现“nonexistent-lease/token=999 被接受”。现从权威 Task/Run 关联解析 owning Task，复用 durable worker 同一 dispatch lease_key 和 LeaseManager，在接受事务内检查 holder、精确 token 与期限。旧 owner 首次提交新步骤、幂等重投、伪造高 token、无关 Task 租约都拒绝。worker 仅将原 key 格式提取为共用函数，未改变现有 key 或调度行为，没有平行决策 owner。
2. **[P1] 设计承诺的 outbox 没有实现。** 初稿提交后 durable_event_log 接受事件数为 0。现决策行与 `agent_decision_accepted` envelope 同事务写入，Task 引用来自真实关联；幂等重投不重复事件，事件写入后再注入故障也会回滚整笔接受。事件只携带关联键/版本/摘要，不复制业务参数或输出。
3. **[P1] 接受内容可以在存储外被修改。** frozen dataclass 并未冻结嵌套 dict，原 InMemory 返回同一对象，修改返回的参数会改变后续恢复事实。现提交前、返回、lookup 和导出均隔离 JSON 快照；SQL 恢复校验 schema_version 与完整内容散列，损坏显式失败，不作为 miss。
4. **[P2] 恢复忽略输入/配置版本。** 初稿相同步骤携带新版本仍返回旧决策。现先冻结输入引用，恢复发现版本变化立即冲突且不问模型；responder 改其副本也不改变被冻结版本。冲突异常只提供内容摘要，不暴露原始输入引用。
5. **[P2] 内容验证与既有词汇不一致。** 初稿只校验 dict/重复键，允许空动作名/键、非有限 JSON、未知副作用类别直接持久化（测试中的 `write` 也不是现有枚举）。现复用 SideEffectClass，拒绝非法记录及未知版本；记录 hash 覆盖身份、版本引用、输出与有序动作内容。公共编码方法和错误类型同步更新，没有类型抑制。
6. **[P2] 恢复测试不是真正跨进程。** 原 PG 4 个用例仅重建 store/engine，却把“新实例”视为“新进程”。现使用两个独立 Python 子进程：写者提交后 os._exit，读者仅凭 PG 找回原输出/参数/动作键/接受时间，模型零调用。另有两条连接的同步竞速和组件候选竞争测试，冲突不重问模型。
7. **[P1] 迁移回退会删除接受事实。** 原 downgrade 直接 drop 表。现该 revision expand-only，破坏性 downgrade 明确拒绝；PG 上验证迁移列/类型/nullable/唯一约束与包 metadata 一致，并插入实际行后证明拒绝回退保留数据。
8. **规范与任务证据同步。** 设计不再把新实例当新进程，不再把决策行 token 当租约；delta 补齐版本/快照、权威租约、同事务事件和回退要求，并澄清 core 既有接缝与 Runtime-owned hook 的范围；Runtime 扩展清单登记新接缝。主 specs 和既有功能成熟度没有提前改写。

## 实际验证

| 检查范围 | 结果 |
|---|---|
| `scripts/dev.py check all` | ruff check 全过；format 1585 文件；mypy 940 源文件无错误 |
| Runtime/durable 包全部测试 + Runtime 自足/kernel purity/domain 分层 + durable contract/drift pins（未配置 PG 的组合） | **197 passed, 6 skipped**；5 个新 PG 用例明确未运行，另 1 项为 SQLite 无法满足的既有 PG 并发用例 |
| 新接受决策共同契约 + 新 PG 专项（SQLite SQL/InMemory/PostgreSQL SQL） | **74 passed**，其中 PG 专项 5 项均实跑，无跳过 |
| 配置专用 PG 后 durable 全套双数据库回归（最终 dispatch lease 版本） | **153 passed, 1 skipped**；唯一跳过是 `test_postgres_reordered_commits_are_not_lost` 的 SQLite 分支，PG 分支通过 |
| 新 Runtime 组件 + PG 专项定向复跑（含实际记录回退保留） | **16 passed** |
| `openspec validate step6-accepted-agent-decision --strict` | valid |
| `git diff --check` | 通过 |

PG 复核重用既有专用实例，通过 IPv4 和短连接超时连接；新测试每次创建唯一 schema，仅清理自己创建的 schema。durable 全套也在独有外层 schema 中运行，不删除共享 public 表或其他项目数据。本次未新建、删除或重启容器。最初 localhost 测试连接等待曾终止并以明确地址/超时复跑，不将中断运行计入通过结果。

## 支持范围

- 只实现单个带动作的决策接受/恢复；空动作最终完成、多轮循环、工具派发、审批、Child Run、Plan Revision 和入口切流仍不在本切片内，1.3.11l 保持 planned/unverified。
- lookup 返回原事实不等于获得当前工具派发权；实际执行仍需当前身份、授权、预算、取消状态、审批和既有 Action Ledger 判定。
- 模型提供方由宿主注入，脚本 responder 只证明确定性断言。提供方适配器应只提交可见输出，不注入 vendor 隐藏推理；本次没有真实模型质量或供应商数据流认证。
- PG 验证覆盖本 revision 与包持久化语义，不代表平台全链生产迁移、所有入口或整个 G2 已获认证。CI/PR 检查尚未执行。
