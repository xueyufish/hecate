# Design

## Context

AgentVersion 已有冻结配置、内容哈希和资源引用清单。评测引擎已有 agent_definition 注入面，workflow/three_layer 已有 EntryExecutionService。复用这些接缝即可绑定评测和发布，无需改 Runtime 或 durable。

## Goals / Non-Goals

目标是证明发布消费的 Run 实际评测了同一 AgentVersion，且配置/任务后续修改不能改变证据归属。历史记录保持未绑定，租户边界和失败原因明确。

不建立完整发布清单、签名、部署激活、灰度或回滚平台；不冻结知识库内容、工具实现和现行资源治理策略。

## Decisions

### D1: Run 保存身份和执行输入，任务只是声明

新增 nullable `agent_id`、`agent_version`、`agent_config_hash`、`agent_snapshot`。快照保存 own config、pinned_refs、ref_manifest、workflow_version；hash 与 AgentVersion own config 的 canonical hash 一致。绑定在实际开始阶段一次性保存。重复绑定要求身份/哈希一致且快照完整；已终止 Run 不允许重跑或重新绑定。修改任务声明版本、清除声明、重定向 Agent 或删除任务均不改写旧 Run。

候选查询直接按 Run 的 Agent、版本、哈希、workspace 和完成状态过滤，不再 join 任务当前 config。历史字段均为 NULL，不靠当前配置补造历史。

### D2: 同一注入面按快照执行

纯函数 build_execution_definition 深拷贝版本快照。类型明确的 AgentVersionExecutionDefinition 在适配器中覆盖 persona（包括空值）、模型和支持的参数、工具名单、知识库引用、技能名单及 ref_manifest；其他 agent_definition producer 保留已有覆盖方式。工具从快照名单加载而不是先按草稿过滤，资源必须在允许的 workspace 中解析，缺失 pin 不静默降级为 live。

chat 使用既有直接调用；workflow/three_layer 通过既有 EntryExecutionService 执行冻结模式，workflow 传精确 workflow_id/version。workflow 入口的技能格式化也使用冻结技能名单，tool registry 的 SkillLoader 同时接收名单和 manifest，L1 与 load_skill 的 L2 解析使用同一版本绑定。workflow 节点执行参数继续由已固定的 graph 控制，three_layer 使用现有入口支持的模型配置，不新增模型/Runtime 参数接口。

绑定评测忽略 item.generated_answer 的缓存，逐项实际生成；调用失败使 Run 失败，空数据集不能成为版本证据。未声明版本保留既有 live/预存答案行为。运行、数据集、任务、Agent 和版本须在同一 workspace；已删除/不存在 Agent 或版本拒绝执行。

### D3: 发布调用方强化证据前置条件

共享 publish_gate.py 不变。Agent 调用方增加 version_binding 前置结果：无匹配候选即失败，不依赖用户是否另开 require_run。回归比较使用 published_version 的匹配 Run，缺少匹配 baseline 时报告 missing_matching_baseline。漂移使用共享 live_dataset_hash 与 Run 的 dataset_snapshot；缺少必需快照时报告 missing_dataset_snapshot。

未配置门禁仍直接发布。warn 仍放行但返回实际失败报告。force 只有在 require 阻断时成为 bypass，必须先成功记录操作者、版本与完整门禁报告；审计失败不移动 published_version。响应和审计均明确 passed=false、bypassed_by_force=true。

### D4: 增量迁移与证据保留

迁移 017 接到 7a6e5d4c3b20，新增 nullable 字段和与 ORM 一致的索引。无 backfill。没有绑定证据时可 downgrade/re-upgrade；出现任一绑定字段后 downgrade 明确失败，保留已写证据和 schema revision。应用回退保留增量列，不自动删除历史证据。

## Risks / Trade-offs

- 当前授权、Memory policy、技能治理/发现池仍为 live，可收窄能力；Skill pin 固定内容而不恢复已撤销权限。
- 未 pin 的技能、工具实现和知识库数据未被此切片冻结；变更这些资源需要后续部署组合/资源版本治理能力。
- 与其他 worktree 合并时需再次核对 Alembic 唯一 head。本次本地 PostgreSQL 迁移证据不能替代合入后的 CI。
- 验证使用 LLM 替身，证明配置和调用路径，不证明真实模型质量或外部 provider 生产可用性。
