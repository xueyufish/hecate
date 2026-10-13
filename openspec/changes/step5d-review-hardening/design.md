# Design

## Context

见 proposal.md。入口已有 savepoint 登记、Run 投影和持久事件 sink。HTTP 目前自行映射流式事件，非流式校验输出在入口服务返回后发生。Run.deployment_id 必填，且部署属于 AgentVersion；独立 workflow 没有合法的对应部署。

## Goals / Non-Goals

**Goals:** 修正真实入口的结果、关联、摘要、事件和 workspace 语义，给出能暴露原问题的回归。

**Non-Goals:** 不创造虚拟 Agent/Deployment，不修改 Run schema，不翻转 G3 默认开关，不把 Stub 轨或内存后端称为真实模型/安装认证。

## Decisions

- 入口 execute 接受结果处理回调，最终修复/校验在成功回写前完成。HTTP 为命名消费者；沿用 Callable，不新增 Protocol。相比事后覆盖 succeeded 投影，这让无效输出从未被记录为成功。
- 入口服务消费每条流式原始事件时经同一 RunEventMapper 落库，仍向调用方返回原始形状；HTTP mapper 仅作渲染，不接 sink，避免双写。同步事件仍用相同 mapper；缺关联先剥离内部字段再返回。相比 HTTP 独占持久化，这也涵盖共享服务的其他流式消费者。
- 流式摘要从 message.content 累积，限制为已有摘要上限；values 中的最终 assistant 消息可补摘要。异常转换保留 correlation，缺关联的异常也附安全 missing 标记。
- workflow execute 增加 input_data，仅适用于 workflow 模式，将显式 channel 值叠加到 assembled.initial_input；保留运行器生成的 sys/内部身份通道，不允许输入覆盖它们。任务侧按调用方 workspace 校验 live workflow，工具 registry 使用共享装配。
- 独立 workflow 仍可执行，但 Task-only 结果明确为 missing（保留真实 task_ref），文档恢复 Run 登记和端到端认证缺口。该选择保留已有配置兼容性；若用户选择强制执行 Agent，须先更新本方案与规格。
- 支持矩阵区分功能回归与安装/升级认证；测试路径逐一核对，缺实际证据的状态改为未认证。
- 宽回归暴露 workflow callback 测试在后台 wake 完成前撤销执行桩的竞态：后续模型测试读取不到刚 flush 的行，并捕获真实 LLM 凭据错误；同序列单独重跑通过。测试 fixture 在撤销桩前等待本轮登记的后台派发，防止共享数据库跨测试污染；不改生产派发语义。
- studio timing 回归使用局部确定性时钟验证单位换算，允许实际亚毫秒运行产生整数零；不通过睡眠或改变生产计时制造正耗时。

## Risks / Trade-offs

- 输入叠加可能扩大可写通道 → 拒绝 sys/内部通道，并由已编译图的 channel 校验处理未知 channel。
- 同步引擎失败时排空列表尚未返回、事件仅存引擎日志 → 保留为证据边界，文档不得宣称失败信封完整。
- 原请求事务遇未处理异常仍可能回滚登记 → 如实登记，不在共享入口中擅自提交调用方业务事务。
- 独立 workflow 无部署身份 → 显式保留缺口，禁止伪造 Run。

## Migration Plan

无需数据库迁移。既有 workflow input_data 恢复为 channel 参数；跨 workspace 目标在派发前拒绝。全局 G3 默认继续关闭。回退代码保留既有登记表与日志。
