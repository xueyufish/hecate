# 企业 Agent 平台实践对照与 Hecate 方案复核

> 研究快照：2026-09-26。仅依据项目官方文档或官方代码仓库可核对的公开信息；产品介绍中的能力声明不等于已经通过 Hecate 的安全、规模与可替换性验收。具体采购或接入时须固定版本、部署形态和许可重新验证。

本文复核 [Hecate 演进方案](enterprise-agent-platform-evolution-plan.md)，不把竞品实现直接写成 Hecate 需求。表中“借鉴”是设计推论，“观察”是来源明确披露的现状；未公开的内部机制不推断。

## 一、先区分产品类型

这些项目不是同一层的替代品。企业控制平台侧重跨 Agent 的登记、身份、授权、动作治理和证据；harness/runtime 侧重单个 Agent 的推理与工具循环；工作台侧重人机交互与交付物；编排/低代码产品侧重构建应用。Hecate 应接入或约束后三类，不以“功能数相同”为竞争目标。

| 类型 | 项目及官方依据 | 可核对的观察 | 对 Hecate 的裁决 |
|---|---|---|---|
| 企业平台 | [Google Gemini Enterprise Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern) | 将 Agent Identity、Registry、Gateway 与 IAM/策略连接。 | 登记、身份和执行点须形成闭环；企业目录不能只存元数据。 |
| 企业平台 | [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-based-policies.html) | Runtime、Gateway、Memory 分别有资源策略；[Runtime 安全实践](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html)强调防止绕过网关。 | 对动作、记忆和执行资源分别验权；准入必须测试网关旁路。 |
| 企业平台 | [IBM watsonx Orchestrate](https://www.ibm.com/products/watsonx-orchestrate/governance-and-observability) | 官方将跨平台 Agent 治理、可观测、目录及编排作为一组企业能力。 | 保持跨实现控制面定位；不以自研推理循环作为核心差异。 |
| 企业平台 | [Salesforce Agentforce](https://developer.salesforce.com/docs/ai/agentforce/guide/ascript-lang.html) | Agent Script 结合确定性指令与模型推理。 | 审批、权限和发布规则由确定性服务执行；不把专属 DSL 定为平台协议。 |
| 企业平台 | [Palantir AIP](https://www.palantir.com/docs/foundry/ai-fde/security-and-governance) | 关联用户动作权限、批准与审计；[日志权限](https://www.palantir.com/docs/foundry/aip-observability/log-permissioning)另行管理。 | 高风险动作绑定实际授权主体、参数和回执；trace 本身按敏感资源控制。 |
| 企业平台 | [Huawei AgentArts](https://support.huaweicloud.com/highcode-agentarts/agentarts_10_029.html) | 文档描述框架无关的容器化 Agent 运行及隔离。 | 将隔离列为部署等级测试，而非要求 Runtime 采用固定语言或框架。 |
| Harness / runtime | [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/README.md) | 按能力族分包；插件依赖 Service Definition，agent loop 可替换。 | 能力定义与实现分离，并用依赖规则及真实组合测试保持边界。 |
| Harness / runtime | [Pi](https://github.com/earendil-works/pi) | agent core、coding agent 等分包；官方明确默认不提供文件、进程、网络和凭据的权限隔离。 | 可作非 Python 适配候选，但必须外加并验证沙箱、出口与凭据边界。 |
| Harness / runtime | [AgentScope Runtime](https://github.com/agentscope-ai/agentscope-runtime) | 提供 Agent-as-a-Service、沙箱与日志/追踪；其框架支持矩阵按能力区分。 | 采用逐能力认证矩阵，不能将“支持某框架”视为支持全部控制语义。 |
| Harness / runtime | [openJiuwen](https://github.com/openJiuwen-ai/agent-core) | 官方仓库提供 Agent SDK、异步/流式运行、工作流与状态恢复能力。 | 仅接入平台 Task/Run/Action；状态恢复格式留在其 Runtime 内部。 |
| Harness / runtime | [deer-flow](https://github.com/bytedance/deer-flow/blob/main/backend/AGENTS.md) | 明确 harness 与应用层分离，以导入规则阻止不当反向依赖。 | 继续用模块化单体，但从现在起限制跨域实现导入，方便未来拆分。 |
| Harness / runtime | [Hermes Agent](https://github.com/NousResearch/hermes-agent) | 具备模型、工具、技能、记忆和消息入口；Tool Gateway 与消息 Gateway 含义不同。 | “网关”分别命名为协议入口、工具执行点和供应商代理，避免一个对象承担全部职责。 |
| Agent 工作台 | [OpenClaw](https://github.com/openclaw/openclaw/blob/main/docs/gateway/security/index.md) | 官方限定单网关为一个信任边界，不可直接视为敌对多租户边界。 | 可借鉴消息入口与会话体验；多租户隔离必须由 Hecate 自行证明。 |
| Agent 工作台 | [Claude Code](https://docs.anthropic.com/en/docs/claude-code/cli-usage) | 提供权限模式以及工具允许/拒绝设置。 | 人类授权和工具级限制有参考价值，但 CLI 权限配置不等于跨组织审计。 |
| Agent 工作台 | [Codex](https://developers.openai.com/api/docs/guides/agents-api/overview) | 官方托管 harness 支持沙箱、技能、MCP、运行中干预、子任务和会话恢复。 | 参考干预与持久任务体验；能否作为外部后端接入须按其公开接口单独验证。 |
| Agent 工作台 | [Meituan CatPaw](https://catpaw.meituan.com/docs) | 披露跨端任务、并行 Agent、独立环境、技能与高风险操作确认。 | 人类实时观察与独立任务环境应进入团队闭环，不复制整套桌面产品。 |
| Agent 工作台 | [Manus](https://manus.im/blog/manus-sandbox) | 每任务沙箱隔离；协作扩大任务沙箱可见范围时关闭 Connectors。 | 团队新增成员时重新判定连接器、沙箱文件和产物访问。 |
| Agent 工作台 | [OpenWorker](https://github.com/andrewyng/openworker) | 本地优先，多模型接入，并对写入、发送、命令等操作提供审批。 | 证明轻量工作台可借用平台控制；不把本地信任默认迁到 SaaS 多租户。 |
| Agent 工作台 | [CubePlex](https://github.com/cubeplexai/cubeplex) | 描述团队 workspace、分层记忆、持久沙箱与组织治理。 | 团队/任务/资源 ACL 与持久工作区需要分开建模和验证。 |
| 应用构建 | [Dify](https://github.com/langgenius/dify) | 提供工作流、Agent 沙箱、插件市场和 LLMOps。 | Hecate 先治理外部应用/Agent，不重复建设完整低代码画布。 |

上表中的 [Codex 官方资料](https://developers.openai.com/api/docs/guides/agents-api/overview) 描述其托管 harness；它不证明 Codex 可作为 Hecate Runtime 直接接入。[Pi 官方 README](https://github.com/earendil-works/pi) 的权限声明则是接入前必须额外提供隔离层的明确反例。

## 二、横向信号与方案裁决

| 信号 | 证据 | 对现有方案的裁决 |
|---|---|---|
| 可替换接口之外还要证明强制执行路径 | [AWS Runtime 安全实践](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html)、[Pi 权限说明](https://github.com/earendil-works/pi)、[OpenClaw 安全边界](https://github.com/openclaw/openclaw/blob/main/docs/gateway/security/index.md) | 保留 Runtime-independent 方向；在 step7/8 增加旁路测试、能力等级证据与受限降级。 |
| Agent 身份、用户授权与工作负载身份各有用途 | [Google 治理文档](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern)、[AWS 会话策略](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-session-based-temporal.html) | step4/7 分清人类发起者、Agent principal、执行工作负载与委派链，不能由一个 API key 代表全部。 |
| 团队共享会扩大隐式访问面 | [Manus 沙箱与协作](https://manus.im/blog/manus-sandbox)、[CubePlex 团队工作区](https://github.com/cubeplexai/cubeplex) | step12—14 对成员变化触发资源 ACL 和 Connector 再授权；展示每次交接的可见范围。 |
| 扩展资产是供应链风险 | [Dify 插件提交要求](https://github.com/langgenius/dify-plugins/blob/main/docs/plugin-submission-requirements.md)、[Pi 供应链措施](https://github.com/earendil-works/pi) | step11/18 增加风险分级、权限清单和可复核的安装/升级回执；先内部目录，不先做公开市场。 |
| 执行循环可以优秀但不应占据平台契约 | [DeepSeek Harness 分包](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/README.md)、[deer-flow 层间约束](https://github.com/bytedance/deer-flow/blob/main/backend/AGENTS.md)、[Manus 上下文工程](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) | 保留 Pregel 为内置参考实现，外部接口只承诺任务/运行/动作/证据；上下文压缩、循环与优化留给 Runtime。 |
| 观测数据及远程自报不能自动等同审计证据 | [Palantir 日志权限](https://www.palantir.com/docs/foundry/aip-observability/log-permissioning)、[AWS Gateway 策略](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html) | step10 对 trace 做字段级敏感分级；关键事件须记录来源、完整性、实际执行回执与验证等级。 |

## 三、实施顺序的具体修正

### 对现有 Hecate 架构的判断

| 已有基础 | 仍缺的边界 | 对主方案的处理 |
|---|---|---|
| 内置 [Pregel Runtime](../../src/hecate/runtime/pregel.py) 与 [RuntimePort](../../src/hecate/runtime/ports.py) 可支撑现有执行；[MemoryProvider](../../src/hecate/core/composition/memory_provider.py) 已有替换入口 | `RuntimePort` 是 Runtime 调外部能力的方向；尚不能据此证明“平台调用任意 Runtime”。 | 保留内置 Runtime；step3—8 建平台侧执行契约并用异构进程验证，不为重构而先删除 Pregel。 |
| [工具网关](../../src/hecate/tools/gateway)、[A2A 接入](../../src/hecate/channel/a2a) 与 [评估模块](../../src/hecate/ops/evaluation) 已有代码 | 代码路径存在，不等于所有入口与后端都经过同一治理强制点；A2A 任务状态与平台 Task 也不能自动视为一套真源。 | step1 盘点入口；step5/7 把工具、数据、协议入口统一关联 Task/Run/Action；step10 分离评估结果、诊断 trace 与治理证据。 |
| 模块化单体和可提取包有利于渐进演进 | 若业务域共写表、跨域直引实现，后续拆分会重写调用链和事务；若先按竞品建立完整微服务又会增加无用复杂度。 | step2 明确状态 owner、公开接口及依赖检查；step16 以一个真实进程外替代演练验收未来拆分能力。 |

当前 `feature-catalog.md` 与 `roadmap.md` 的后续规划不宜追加每个竞品的专有功能。优先将“部署准入等级、不可旁路治理路径、身份链、任务共享再授权、风险分级制品准入”分别作为现有能力的独立验收项或新 Feature；维持主方案第六节对通用 IDE、训练框架、公开市场、行业知识产品和无限自治团队的延后/外置决定。路线图只修订 M-A 至 M-C 的退出条件，其余里程碑依赖真实接入结果重估。

1. **先做边界验证，再冻结接口。** step1 为每类部署写信任假设和禁止旁路清单；step3 协议保留适配空间；step7 制作强制执行拓扑及负例；step8 用至少一个异构 Runtime 和一个远程黑盒按证据授级。不能凭 SDK 方法名称授予 `hosted_enforced`。
2. **将身份链落实到每一次副作用。** step4 建立人类、Agent、工作负载和 Run 的不同标识；step7 为代理调用签发短期、目标受众限定的委派凭据；工具网关把授权决策、参数、执行回执和撤销状态关联。
3. **把团队任务的数据共享当成新授权。** step12 设计团队/任务/资源 ACL；step13 交接包只含允许字段；step14 在增加协作者、接管或重分派时重新验证 Connector、Memory、沙箱文件和产物权限。
4. **把生态准入前移到发布门禁。** step11 对 Agent/Skill/工具/adapter 制品建立来源和权限 manifest、风险等级及签名/扫描/测试结果；step18 再扩展第三方供给与安装渠道。高风险能力需要针对性审查，不能仅靠包签名。
5. **维持克制的产品范围。** 先证明“异构执行＋统一治理＋团队协作＋证据”的纵向闭环；公开市场、无限自主团队、通用 IDE/低代码画布、训练框架和行业知识产品继续放在条件性或外部实现路径。

这些修正已经映射到主方案的 step1、step3、step4、step7、step8、step10—14、step16、step18 和统一验收矩阵。它们是架构验收条件，不代表竞争产品已在 Hecate 完成接入。
