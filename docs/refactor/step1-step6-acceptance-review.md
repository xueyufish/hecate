# Step1～Step6 实施验收复核

## 结论与基线

基线为本地 `main` / `origin/main` 的 `87520ea`，包含共享执行契约、受管执行环路、动作时授权、持久等待、checkpoint 和工作流回调等后续变更。审查与修正位于 `fix/step1-step6-acceptance-review`，对应 OpenSpec change `step1-step6-acceptance-hardening`；本报告描述该工作树的修正结果，未提交、未推送，尚未成为 main 的交付事实。

**方向继续保留，但不能认定 Step1～Step6 的全部目标已经关闭。** Runtime kernel / 执行应用服务 / Runner 宿主 / 平台登记与投影的边界成立，语言中立契约和可独立安装包已有证据。发现的执行与授权缺陷已修正；平台原生 continuation、完整受管进程组合、合法审批和真实工作流节点仍是关闭门槛。

复核以代码、负例、实际业务 API 调用计数和安装制品为证据。仓库中的计划、已归档 tasks 或测试名称均不单独作为完成证明。本轮不重新调研竞品，不改变既定定位，也不把测试库存业务或具体应用场景纳入平台产品。

## 已发现并修正

| 问题与影响 | 修正 | 验证落点 |
|---|---|---|
| 等待后新 Run 从头执行，前序业务写入拥有新 action key，可能再次写入 | 保存可信前序动作来源，仅等待工具采用新 attempt；原动作结果跨 attempt 按同一 Task 回填；审批在 claim 前 park | `packages/hecate-runner/tests/test_waiting_wake.py`：写入→等待→唤醒，前序写调用一次；wheel 进程统计读/写次数 |
| 外部请求能携带 `_wake_grant` 等内部字段，绕过技术审批等待 | 本地提交拒绝宿主内部字段；受管接受过滤外部内部字段并写入自身可信身份/定义；只有持久唤醒产生 grant | 同文件的内部 grant 注入负例；受管正常派发回归 |
| 唤醒没有 revision CAS，command ID 只比部分字段；重复命令可能绑定不同 token/Run | token、原 Run、issuer、载荷、期限完整绑定；一次性唤醒用 revision CAS，命令状态条件更新；并发同 ID 返回原应用回执 | 并发相同/不同 command ID、错误 token、异体重放；PostgreSQL durable 命令回归 |
| 输入合并污染工具命名空间；唤醒绕过入站 Schema | 仅合并等待工具的参数，保留已有参数；验证 wait fields 与最终工具 Schema；不满足契约保留等待并拒绝 | 输入等待/唤醒及现有参数验证回归 |
| 唤醒提交后在事务外删除 task 级 checkpoint，宕机窗口可能恢复旧等待图 | 新 attempt 使用自己的 checkpoint session；旧 task 级缓存仅兼容原 attempt，不靠事务外删除保证正确性 | 唤醒 commit 后立即重启，执行一次后续动作；checkpoint 恢复用例 |
| 指定 checkpoint ID 读取没有同时限定 session | 查询绑定 session 与 checkpoint ID | `packages/hecate-durable/tests/test_checkpoints.py`：跨会话读取返回空 |
| 已发生但结果未知、回执落盘失败后仍可能执行下一工具；授权拒绝的 Run 可显示成功 | 未知/持久化失败停止后续工具并待对账；授权拒绝停止后续工具并失败；更新原来错误的 SC02 成功预期 | 未知前序写后续工具零调用；分别在读/写结果落盘失败处注入故障；wheel 越权拒绝 |
| 平台命令重放先返回旧回执再查资源；公开 API 丢弃 command ID / wait token | 先核实 workspace/Task，再完整比对命令；公开请求传递 `command_id` / `detail_ns`；保留原时间和 Run | `test_task_control_service.py`、`test_workflow_callback_api.py` 的服务/HTTP 负例 |
| 回调只比较 child ID，附加结果可以替换核实的 child ID/state；异体重放未核验 | 比较完整 BackendRef，平台可信字段最后写入；原请求摘要绑定回执 | 伪造子结果、跨 workspace、状态不符、不同 token 的重复回调 |
| 平台把“聊天历史可加载”当成原生续跑，再次执行模型可能产生不同 Action ID | 删除这种恢复路径；含受保护动作的中断保持待对账，待真正的 continuation 接线后重新验收 | `test_worker_integration.py`：历史存在但无原生续跑，Runtime 零调用 |
| 提交后 Principal/部署失效仍可派发；Run 快照已有但执行读取 mutable Agent 配置 | 执行前重验 Principal 和部署/版本；工具、模型、persona、guardrail 配置和资源引用使用冻结版本 | 排队后撤销主体/改变部署或版本：零执行；修改 Agent 工具不替换 admitted 配置 |
| Runner 重启可能按升级后的工具图/模型或业务端点恢复旧任务 | 接收时保存可信定义摘要，覆盖 manifest、工具顺序/Schema、模型引用和业务派发绑定；恢复必须匹配，等待唤醒在消费 token 前检查 | 定义漂移/摘要缺失：零调用、待对账；等待漂移拒绝且 token 未消费 |
| Lease 有签名但未绑定 issuer、host subject、workspace | 平台签发 tenant；生产 CLI 的实际 Action 门校验完整身份绑定 | 有效签名但其他 issuer/subject/tenant：业务写 API 零调用 |
| 宿主 Task 列表/事件/Action 未按 owner 隔离；重启后新 wake Run 无法查询 | 所有 Task 读取检查 owner 与当前可信数据域；Run 从持久关联或历史事件反查 Task | 跨身份列表不可见、具名读取拒绝；wake 后再次重启能查询新 Run |
| 上传独立来源或单个无法投影的流，会阻塞其他受管结果；只扫描当前 Run 会漏历史流 | 仅上传 managed 来源，发现各历史事件流，游标分别保存，单流拒绝后继续其他流 | `test_managed_execution_loop.py`：首流拒绝不阻塞次流，独立任务不上传，重连补齐 |
| SC03 的“批准写入”测试实际只测普通写完成后的重启 | 保留并准确命名终态写切片；补非 editable wheel 的真实审批等待、kill/restart、唤醒与重复回执；构建失败保留诊断 | `tests/scenarios/test_sc03_local_approval_restart.py`；场景清单仍 planned |
| 重启测试的 fixture 只停止原 Runner，替换后的子进程泄漏 | 清理 fixture 中当前 Runner，而非已终止的原实例；清理仅限定本轮测试子进程 | SC03 终态写与审批重启场景 |

新增反例在修复前失败，分别证明前序写重复、内部 grant 注入、命令异体重放和跨 session checkpoint 读取。后续回归也纠正了原来将授权拒绝视为执行成功的断言；没有用放宽权限或忽略失败来使测试通过。

## Step 状态与架构判断

| 范围 | 已有证据 | 尚不能据此宣称 |
|---|---|---|
| Step1 | 基线、入口/能力归属、S/P/SC 验收清单与负例已形成；本轮保持原 G 门槛 | 全部部署数据流已核实、外部供应商已认证、多 Agent 收益已验证 |
| Step2 | 定位、能力域、catalog/roadmap 与边界处置规划已形成 | 所有标作可替换的服务已完成替换或独立生产发布 |
| Step3 | 语言中立 Schema、互操作样例及一致性门禁通过 | 任意 Python/TypeScript/Go/Rust 后端都已完成持久执行与治理认证 |
| Step4 | Principal/Deployment/Task/Run/BackendRef 登记与本地身份边界成立；本轮补执行时复验 | 登记即授权、登记的外部部署即可真实调度、托管服务具有相同控制能力 |
| Step5 | 可安装 Runtime/Runner、共享执行应用服务、既有平台入口迁移和只读 SC01/SC02 | Runner 固定图已是通用生产 harness；所有入口和外部 adapter 已统一通过完整治理验收 |
| Step6 | SQL 存储/worker/fencing/outbox、动作账本、等待/回调原语、受管环路及独立进程切片已交付 | G2 全关闭、完整受管命令/恢复、企业审批、真实工作流图接线和完整 PG 宿主进程矩阵 |

保留模块化单体和可单独安装的宿主；平台保存登记、命令、治理语义和证据投影，执行后端拥有本地生命周期、checkpoint 和工具派发事实。不同语言通过公开协议接入，不能导入平台 ORM 或依赖 Hecate Python 内部对象。技术预览的固定工具图和库存 API adapter 只负责验收；未来供应商 runtime/memory/eval 的选择不绑定这些样例。

## 仍需实施的关闭门槛

1. **Step6a/6f：**非 editable Runner wheel 对接真实平台 HTTP，覆盖接受回执丢失、持久投递、执行、结果上传、重连、kill/restart 与迟到回执；随后在 PostgreSQL 上运行完整宿主组合。现有 ASGI 与存储测试不能替代。
2. **Step6c 与 Step7：**受管命令投递、宿主命令回执、新 wake attempt 的平台映射和合法审批接线。当前上传器可扫描历史流，但平台仍只接受已确认的 Task/Run 映射；不能宣称新宿主 Run 已能自动映射。
3. **Step6b 与 Step7：**工具/动作级权限、审批参数绑定、职责分离、撤权与断连陈旧窗口、多写动作期间续租。当前 HMAC 是预览信任机制，每个 Lease 消费一次，不承担全部企业授权保证。
4. **Step6d：**平台共享执行的真实 native continuation 与冻结动作关联；缺少时保留待对账。继续验证外部写成功但回执失败、未知结果、故障后参数/工具冲突的真实入口矩阵。
5. **Step6e：**确定性 workflow 节点或具名 workflow adapter 发起子任务、持久等待并消费回调，验证父子各自重启。本轮没有把 monkeypatch dispatcher 认作编排产品入口。
6. **Step7/10：**证据故障时只读继续或整体停止的正式策略，中心上传缓冲上限、缺口与保留期限。SC06 整体保持未验收。

以上继续留在原责任 Step，不挪到最后认证阶段隐藏。详细步骤已同步到 [演进方案](enterprise-agent-platform-evolution-plan.md) 的 step6a～step6f。

## 验证证据与限制

测试从审查 worktree 运行；显式设置 `PYTHONPATH` 指向此处的 `src`、Runtime、durable 和 Runner，避免共享 editable 环境指向旧分支。安装制品测试单独构建本工作树 wheel，在新 venv 中以 `python -I` 启动并移除源码环境变量。

- 受影响范围、Runtime、分层与场景清单完成核对。大范围收口暴露带请求体 GET 拒绝时的 TCP 重置；已补读完声明请求体再返回 405，随后完整 Runner 回归通过，包含该反例。默认回归中的条件跳过不作为认证；PostgreSQL durable 另行启用真实数据库运行。
- PostgreSQL durable 包通过，保留方言限定的条件跳过；使用专用 PostgreSQL 参考容器与专用测试库。
- 完整 Alembic 升级链/ORM 漂移通过，使用同一隔离容器中的独立临时库。
- 安装制品 SC01/SC02/SC03/SC06 已执行。并行构建时曾在 wheel 构建阶段失败；加入构建诊断后串行复测审批进程用例通过。构建阶段失败不能算成功运行；最终该用例确实运行到了等待/重启/副作用断言。
- ruff、format（平台、测试与修改包）、mypy（平台与修改包）、OpenSpec strict 均通过；`git diff --check` 通过。精确结果、命令与历史失败保存在该 change 的 `verification.md`。

本机测试解释器为 Python 3.14.6，并非 CI 的 Python 3.12；本轮结果不替代 CI 目标版本检查。没有运行全部应用测试，也没有认证真实模型供应商或企业环境。测试中已有 session factory 收集提醒、持有背景 coroutine 的测试清理提醒及 HTTP 常量弃用提醒，报告不把它们当作生产执行证据。

## 升级与回退

1. drain 宿主并备份持久账本；本次无新表 migration，平台仍应运行已有完整 Alembic 链。
2. 无执行定义摘要的旧非终态 Runner 任务不自动执行；等待任务拒绝无原版本证明的唤醒，终态可查询。由原版本宿主完成或进入显式对账，不能用当前摘要补签旧任务。
3. 平台与受管宿主升级后重新 pull 携带 tenant 的 Lease；旧 Lease 不可继续授予受保护动作。
4. 保留原动作来源、已消费 token、命令绑定和 attempt checkpoint。回退不能恢复“聊天历史即 checkpoint”、重新执行前序写入或改写终态回执的路径。
