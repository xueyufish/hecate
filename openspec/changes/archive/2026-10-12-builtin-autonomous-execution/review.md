# step5e 实现复核

## 结论与边界

依据本 change 的 proposal、design、两份 delta spec 和 tasks，并对照
`docs/refactor/enterprise-agent-platform-evolution-plan.md` 复核。GLM 的实现存在停止、
回放、结构化输出和父子任务推进缺陷，不能凭原 tasks 全勾选认定完成。本轮已在原工作树
修正实现并补充可执行证据；没有提交、推送、创建 PR 或归档。

真实模型记录明确为 `skipped`，不能证明模型质量、收益或独立宿主自主执行已通过。
step5e 保留为技术切片交付，整体生产验收尚未完成。

## 缺陷与调整

| 严重度 | 原实现缺陷 | 调整及任务映射 |
|---|---|---|
| P1 | Pregel 只记录 unknown 回执，仍将错误作为工具结果交给模型；direct 重放异常可能被吞掉 | 首次 unknown 和恢复 review 均抛出终止信号并带动作关联；已有并行派发完成后再传播；配置重试不得覆盖终止条件。覆盖 2.1、2.4、3.2 |
| P1 | 引擎结构化输出修复重新执行完整图，可能重复业务写入 | 只修复最终模型回答，禁用工具，最多一次；每次修复仍检查预算和取消。覆盖 4.2、4.3 |
| P1 | Workflow 子回调可使用调用方提交的 summary/step_index 伪造产物或推进位置 | 优先读取持久化子终态结果，核对子任务父引用与当前步骤；回调 receipt、wait token 消费、父唤醒同事务提交；稳定子提交幂等键。覆盖 5.2、5.3、5.4 |
| P1 | mode/output_schema/artifact 只在父步骤声明，没有可靠传到子执行；无真实失败/重启组合测试 | 保存 workflow_step，给子模型传输出要求，父验证已提交内容和产物；记录 execution/validation 失败来源，按 fail/await 路由。覆盖 5.1—5.4 |
| P1 | 快速子任务先完成、父任务后保存等待时，早到自动回调被跳过，父任务可能一直等待 | 等待提交后复查已提交的子终态并走相同验证回调，复用原 command id 和步骤 receipt。覆盖 5.3、5.4 |
| P2 | budget 捕获只包围异步生成器创建，推进时的错误逃逸；迭代上限返回空值 | 同步返回明确停止类别，流式发送终止 error 与 `[DONE]`；模型边界统一取消检查。覆盖 2.2、2.3 |
| P2 | Agent API 丢失 output_schema，普通模型路径及部分流式路径绕过校验，空内容也可能放行 | 两个请求入口保持字段，声明 schema 的最终内容验证通过后才发送；无效最终内容不泄漏。覆盖 4.1—4.3 |
| P2 | 未解析的 schema 引用可导致服务器异常，外部引用可能触发网络读取 | 新增共享离线校验：只接受本地 fragment 引用；无法解析的本地引用成为显式输出校验失败；聊天与 Workflow 一致 |
| P2 | 匿名会话共享身份，裁剪历史后轮次重用会碰撞；未完成工具轮重入未先回放 | 匿名请求分配会话标识；逻辑键增加历史摘要；保留旧 call-id 键读取；恢复未完成工具轮先回放真实回执。裁剪后恢复只证明不错误命中，不能认证副作用不重做。覆盖 3.1—3.3 |
| P2 | Agent SSE 返回后就释放会话锁 | 与全局 chat 入口一致，锁持续到流结束或关闭；声明 schema 的延迟执行也受锁保护 |
| P2 | 基线为另写循环，rubric 未检查真实反馈参数/停止分支，成本缺失可能被当成零；矩阵与 ADR 仍为骨架 | 真实轨调用实际 direct/Pregel 链路，记录各轮、工具参数、停止、延迟/用量/成本及阈值；成本未知显式保留未知；无凭据生成 skipped 记录；重写矩阵及有限结论 ADR。覆盖 1、6、7.1、7.3 |

## 验证证据

- 较大范围首次回归：2225 passed、31 skipped、9 failed。停止语义相关旧断言已调整为
  检查终止类别、动作引用及副作用计数，而非接受可继续推理的 review 工具消息。
- 环境核查：本工作树缺少已声明的 `hecate-memory`，通过 `uv` 安装本地包后契约测试通过；
  Windows shell 用例依赖 `cat`，仅给测试进程补充 Git 自带工具目录的 PATH；未改动相关实现。
  缓存 TTL 时序用例复跑通过。Runner 端口文件可在写完前被读到空内容，两个失败用例复跑通过；
  未将这次通过视为该测试 harness 竞态已修复。
- 受影响较大范围回归：2179 passed、31 skipped、零失败，范围为 channel、execution、runtime、
  runtime/governance 包及普通执行、拒绝、审批、缺失结果、S12 与基线报告场景。
- G1 REST 授权、跨域依赖与终止重试专项：76 passed。
- Schema/Workflow/HTTP-SSE 专项：139 passed。
- 快速子任务修复后的混合回归首次为 586 passed、30 skipped、6 failed。原因定位为新增
  Workflow 测试复用了直接改写 `_spawn_background` 的既有 helper，没有像原调用方一样
  使用 monkeypatch 恢复全局函数，导致后续 HTTP 回调用例不能启动后台任务。已在所有新增调用方
  使用测试级 monkeypatch；按 Workflow 测试先运行、HTTP 回调随后运行的同进程顺序复核：
  **54 passed、零失败**，包含快速子任务、伪造/重复/迟到回调以及最新 S12 Schema 场景。
  以上计数属于不同运行且有重叠，不相加为唯一用例总数。
- `ruff check` 与 `ruff format --check` 通过；`mypy` 覆盖 src 和 runtime/governance/durable/runner
  包，774 个源文件零错误；文档交叉引用可解析。
- 真实轨记录：`tests/scenarios/baselines/autonomous_multiturn_baseline.json`，
  `_meta.status=skipped`、`gate=false`、`runs=[]`；原因是缺少 `OPENAI_API_KEY`。
  基线脚本以注入 provider 验证两条实际路径及 normal/needs_review/budget/cancel 分支；
  这只是采集器协议验证，不能改写 skipped 记录为模型质量通过。
- Windows 上 OpenSpec 能列出该 change，但 `status`/`instructions apply` 报
  `Schema 'spec-driven' not found. Available schemas: spec-driven`。已直接阅读现有 artifacts
  继续复核，没有凭此重新建 change 或自动触发新的 `/opsx-*`。
  `openspec validate builtin-autonomous-execution --type change` 已通过；该命令的沙箱 realpath
  限制解除后可正常读取现有 specs。

## 尚未验证

- 真实模型审批等待/恢复后的推理质量、模型成本阈值和真实业务收益。
- 独立 Runner 模型自主选择工具；目前仍为已支持的固定工具计划。
- 任意裁剪历史后受保护副作用的可靠恢复；当前 direct 回放依赖一致的已保存历史前缀。
- Pregel 在只剩成功回执摘要、无 channel 实际内容且无持久 ledger 结果时，仅提供对账标记；
  没有新增真实内容恢复保证。
- 受管部署及 I-Cb 生产认证、审批/恢复全流程认证；本地 SQLite 回归不代替 PostgreSQL 认证。

矩阵与 ADR 中均保留这些限制；本轮不切换默认路由，不抽取新执行内核，不扩大 Runner 交付声明。
