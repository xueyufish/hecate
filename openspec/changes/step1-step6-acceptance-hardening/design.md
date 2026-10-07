# Design

## Context

审查基线为 `87520ea`。动机见 proposal。既有 Runner 使用固定顺序工具图；等待唤醒开启新 attempt，动作 key 由 Run+tool 组成，因此从头执行会绕过前序台账。平台 SessionState 是聊天状态，不是原生执行续跑入口。

## Goals / Non-Goals

保证等待前已完成的动作不重复、未知动作停止后续派发，命令和 checkpoint 不跨资源重放。保持语言中立后端边界；本轮不建设通用恢复引擎或 Step7 完整策略系统。

## Decisions

- Runner 将工具的原动作 Run 来源写入可信持久输入。唤醒保留前序工具来源，仅等待工具使用新 attempt，台账回填前序真实结果。审批等待在领取之前进入，避免把从未执行的审批动作误判为未知写。
- checkpoint 以原 attempt 为作用域；新唤醒 attempt 不会加载旧 checkpoint，无须在唤醒事务外删除。兼容已有任务级 checkpoint 仅限未消费等待的中断 attempt。
- 唤醒用读取到的 revision 做 CAS，并记录原 Run、token、issuer、payload 和期限；重放比较所有这些不可变字段。拒绝外部输入的宿主内部字段。
- 平台控制命令先查授权 task，再比对原始请求；聊天历史不足以证明恢复，含受保护动作的中断保持待对账。
- 回调校验完整 `await_task_ref`，可信 child ID/state 不允许载荷覆盖；重放继续验证原请求。
- 指定 checkpoint ID 必须同时过滤 session ID。受管上传只处理 managed 来源，并发现历次 Run，单个失败流不阻塞其他流。
- 平台执行前再次校验 Principal 状态与 admitted Run 的部署/版本快照，实际执行从冻结版本装配；Runner 持久化 manifest、工具顺序/Schema、模型引用和业务派发绑定的摘要，缺少摘要或配置变化不恢复。
- 生产受管 Lease 绑定 issuer、host subject 和 workspace；同一租约仍只授权一次受保护 Action，连续续租及细粒度策略属于 Step7。宿主 `/tasks` 列表、动作和事件仅对匹配原 owner/数据域的当前可信身份可见。

## Risks / Trade-offs

- 平台自动恢复收紧 → 显式保留 G2 缺口；后续接原生 continuation 再开放，不能重复调用模型冒充恢复。
- 已有内部状态格式 → 保留安全兼容路径；已消费等待的新 attempt 忽略旧任务级 checkpoint。
- Step7 审批策略尚未接入 → 文档声明本地 owner 唤醒仅为技术路径，不能声明企业审批认证。

## Migration Plan

先 drain 并备份宿主账本。无需变更现有表结构。checkpoint 旧行保留但新 attempt 按独立会话读取；旧受保护中断任务进入对账，管理员不得手改 queued 绕过。回退须保留修复后的请求绑定和动作安全规则。

已有无执行定义摘要的非终态 Runner 任务进入待对账，等待任务保持等待并拒绝无证明的唤醒；终态结果仍可查询。不得给历史任务补上当前摘要后声称已验证原版本。旧无 tenant 的授权租约需要从平台重新 pull；不复用历史租约。
