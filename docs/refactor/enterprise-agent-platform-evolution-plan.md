# Hecate 企业 Agent 治理与协作平台调整方案

> 文档性质：重构方案定稿，固定本轮架构决策、实施顺序和退出条件；不表示规划能力已经实现，也不自动启动任何实施 change。新证据可通过后续 ADR 调整决策。
> 复核基线：2026-09-27，本地 `main` 与本地远程跟踪分支 `origin/main` 均为 `8a8a84cd95d0c129beed6fb82df3f695657936db`；本轮未另行核验远程服务器状态。已纳入 #174—#182 的合并结果。实施前重新检查目标分支和在途 change。
> 供应商原则：PI、Mem0、LangSmith 和 OpenAI Agents API 仅是讨论或验证的例子。平台必须允许企业选用其他项目、自研服务、不同语言实现、自托管或供应商托管的执行服务。
> 竞品复核：[企业 Agent 平台实践对照](agent-platform-practice-review.md)记录官方资料、可借鉴边界与本方案修正；竞品宣称不等于 Hecate 接入认证。
> 场景能力复核：依据用户提供的 `Problem_Lab.xlsx` 的 `Problem Checklist` 工作表中 P01—P08 实验任务；该工作簿是能力验证输入，不是产品功能目录或企业真实业务需求证明。
> 独立交付增量：用户已提出车商 App 私有化交付这一具名消费者。本次在工作副本 `fe4817d` 上修订计划，保留既有基线和完成记录；新增独立运行要求是待实施目标，不代表已通过安装、运行或生产认证。第二节测试结果属于前轮核验，本次未重跑产品测试。

## 使用方式

保留 step1 至 step19 作为工作包标识，按第四节的依赖和第七节的迭代切片推进，不要求一次完成整个工作包才验证。每一步给出代码落点、操作清单、交付物、验收和迁移策略。只有退出条件满足，才将对应步骤记为完成。步骤名称是建议的 OpenSpec change 名称，不是已创建的 change；一个步骤过大时，拆成独立 change 和 PR。

本次仅调整此方案。`positioning.md`、`feature-inventory.yaml`、`feature-catalog.md`、`roadmap.md` 和源代码的后续调整，是各步骤的交付内容。仓库实际文件名是 `feature-catalog.md`，不是 `feature-catalogue.md`。第二节是本轮代码快照，实施状态以关联 change、测试证据和功能清单为准。

先完成 step1 与当前交付有关的剩余基线及独立消费增量，再做 step2 的文档对齐；step3 即启动真实外部后端的窄范围验证，step8 才授予正式接入认证。step5 提前交付可独立安装的执行组件和宿主，生产写入还须通过 step6/7、最小 step10/11 及对应 step16。不要先整体移动目录、重写 Runtime、建设微服务集群，或一次性创建全部接口。托管组合详查和团队成本比较可随所属步骤补齐，不阻塞无关部署形态。

**本轮结论：**总体方向保留，实施路径需要收紧。已合并修复建立了较好的认证、回执和 Runtime 边界基础，但尚未实现统一控制面或独立演进的组件体系。此次调整重点是复用这些成果、补齐真实执行边界、提前异构验证和最小发布门禁，并把生产验证贯穿实施过程。

**新增完成判据：**实施相关步骤后，企业应能用自选组件完成工作簿覆盖的知识接入与检索、工具动作、权限治理、确定性流程与 Agent 协作、评测和故障诊断实验；平台对每个组合只承诺经测试的能力。实验里的金融流程、Ontology、特定数据集、固定算法与指标阈值属于测试内容或可选实现，不直接纳入平台核心。step17 跨组织联邦、step18 完整生态分发和 step15 自治理不构成这批场景的共同前置条件；已交付范围仍须通过 step16 对应验证。

**独立消费完成判据：**业务 App 可只安装所需的 Hecate 执行组件及适配器，在没有管理平台、Studio 和平台管理数据库时冷启动并完成声明范围内的执行。连接控制面是可选能力；切换到受管模式不复制执行内核、不改变业务 API 的最终授权责任，也不自动搬迁活跃 Run。RAG、向量库、Memory、模型网关、Sandbox 和集中评测/观测均按需要启用。独立运行与整体后端可替换分别验收，不能用一个目标的完成代替另一个。

## 一、目标定位与不可变边界

### 本轮确定的定位

Hecate 是可自托管、支持云端部署、与模型及运行时无关的企业 Agent 控制与协作平台。企业可以注册、部署或接入不同来源的 Agent，包括企业自托管 Runtime 和供应商托管的完整 Agent 服务，并替换记忆、知识、评估、观测、策略、调度、审批、目录和证据存储等实现。Hecate 的核心不是必须自研这些服务，而是定义跨实现的治理语义、信任边界、强制执行点和可验证证据；默认实现、企业既有系统和第三方服务都可通过契约接入。

Hecate 的核心交付价值是：企业能够回答某个 Agent 归谁负责、获准做什么、以谁的权限执行、使用了哪些版本和数据、谁干预过、产出了什么，以及出现异常时如何停止新动作、接管和追责。

**最高原则：不管企业最终使用什么 Agent 技术，都能通过契约和适配，让它以统一、可治理、可评估、可审计的方式进入企业系统。**这是技术中立的接入目标，不是对任意后端无条件担保：能登记不等于获准访问生产资源；无法验证身份、受保护动作路径或必要证据的后端，只允许隔离试验、观察接入，或拒绝对应用途。统一的是企业规则和事实表达，各后端实际可提供的控制能力必须显式保留。

车商 App 是已提出的独立消费需求，用于验证私有化产品能否按需使用执行组件；具体 Agent 功能、真实业务指标和生产负载尚未确认。先用模拟库存 API 和合成数据验证集成，不在 Hecate 实现库存管理、定价规则或客户管理。结构化库存查询先走业务 API，需要文档检索时再接入可替换 Knowledge 服务。技术验证不能证明业务 ROI、真实用户体验或行业合规，后续接入真实场景时补充业务验收数据。

### 交付边界、运行模式与独立消费

Hecate 继续以技术中立的企业治理与协作为核心，同时交付可被垂直产品独立消费的参考执行组件。下表是交付边界，不要求对应不同仓库或全部微服务化；某项算法、数据库或供应商 SDK 不得成为所有边界的共同必装依赖。

| 交付边界 | 最小责任与调用方 | 可选部分与排除项 |
|---|---|---|
| 中立契约与客户端 | 按能力发布 schema、请求/事件/回执样例及版本规则；业务 App、管理平台和各语言 adapter 均可使用 | SDK 是便利封装，不安装完整 Hecate；不含 ORM、Pregel DSL 或供应商私有状态 |
| 独立执行组件 | 内置 Runtime 加执行宿主；宿主加载定义、装配已选 adapter、验证调用身份和权限、管理执行状态与本地证据；为业务 App 提供服务入口 | 模型实现、Memory/RAG、Sandbox、持久化及安全服务可配置；独立宿主不需要 Studio、组织目录或管理平台启动 |
| 企业管理平台 | 登记/部署、集中准入、发布、团队、人工干预、评测管理及证据查询 | 可管理内置和外部 Runtime；不要求客户先部署管理平台才能使用参考执行组件 |

**执行内核与执行宿主：**Runtime 包含执行机制和 Agent 执行语义；宿主负责配置、依赖装配、服务生命周期与本地执行管理。业务 App 的语言不受 Python 限制：首个独立交付采用 HTTP/JSON 服务入口；Python 进程内嵌入复用同一装配和应用服务，有具名消费者再认证其进程生命周期与隔离限制。独立进程不自动等于不可信代码沙箱；执行任意代码或浏览器工具时，另启用通过验证的隔离后端。

| 运行模式 | 必须部署 | 定义、授权与状态的来源 | 首次交付 |
|---|---|---|---|
| 独立运行 | 执行宿主、已选模型/业务 adapter、该 profile 必需的存储 | 本地固定版本制品；业务 App 的已验证身份及本地策略；宿主拥有本地任务与执行事实 | step5 技术预览；I-Ca 完成限定生产闭环 |
| 受管运行 | 同一执行宿主或合格外部后端，加可达或按策略暂时断连的控制面 | 控制面发布期望配置与授权；执行方保存实际绑定、执行事实及命令回执；受管断连不自动降为独立授权模式 | I-Cb 完成连接、断连与重连认证 |
| 完整私有平台 | 客户环境中的管理平台、执行组件及选用的基础设施 | 本地企业管理域拥有集中治理，执行方仍独立管理执行事实 | 按 step16 对应完整平台 profile 认证 |

网络模式与上表分轴登记：允许指定外部服务、仅企业内网、完全隔离网络。不连接 Hecate 控制面不等于不能联网；完全隔离网络须另备本地模型、依赖/制品及信任材料。初始认证覆盖独立运行和受管运行所需的实际组合；离线安装/完全隔离网络只在其模型、制品与测试环境具备时授予支持，未通过时明确未支持，不阻塞其他 profile。

**状态与信任规则：**

- 独立模式的任务由宿主分配带部署作用域的标识，拥有任务、Run、Action 和执行事件的权威本地记录，不依赖平台表。受管模式下，控制面拥有平台 Task 的责任/验收、部署期望值和命令请求；执行方拥有实际 Run 生命周期、checkpoint 和动作结果，平台保存其带来源/序号的投影。每个字段一个权威写入方，不让双方直接改同一状态。
- 本地任务接入控制面时显式登记来源部署与 ID 映射；历史事件只能按其实际来源导入，不追授平台审批。首版连接治理适用于新 Run，历史 Run 可只读观察；已有 Run 不因注册或重连重新执行。若以后迁移调度权，必须先 drain 或封禁旧投递方并更新 ownership，不能并行双主。
- 独立模式由客户配置的本地信任根和业务身份系统授权；受管模式按中心下发的限域、限时授权执行。断连不能自签权限、更换信任根或恢复已撤销授权；高风险动作要求当前在线判定，或预先批准且当前有效的本地审批机制。离线期间中心撤权不能保证立即到达，须声明最大陈旧窗口、授权期限和过期后的拒绝行为。
- 业务系统拥有最终业务权限与状态机。Agent 调用携带可核验的服务身份或 on-behalf-of 上下文，库存读写通过业务 API；生成建议不等于获准修改业务状态。基础库存样例不依赖向量数据库。
- 本地证据持久化与集中上传分开；上传字段和目标须明确允许，默认不外发 prompt、业务记录、产物或 trace。使用外部模型的数据流独立声明，不把“数据库在本地”表述为“所有数据不出本地”。
- 部署 profile 必须说明执行宿主、adapter、存储、模型及信任材料的安装清单、资源实测、备份和支持范围；不预先承诺未测量的硬件下限。可靠任务必须持久化，但不因采用 Hecate 而无条件部署 PostgreSQL、Redis、MinIO、Temporal、向量库全套设施。

### 场景能力覆盖边界

下表把 `Problem_Lab.xlsx` 的实验任务映射到平台保证。**平台保证**是契约、准入、授权、状态或证据；算法和具体服务由默认实现、企业既有系统或第三方组件提供。对外部完整 Knowledge/RAG 服务，内部阶段可能不可见；此时可提供端到端评测，但不能宣称支持阶段诊断。各项验收在第八节落成场景，实施按第五节逐步推进。

| 工作簿问题组 | Hecate 应提供的支撑能力 | 可替换或由场景提供的部分 | 所属步骤 |
|---|---|---|---|
| P01 检索失败诊断 | 可固定知识快照/检索配置并比较结果；在能力允许时关联查询变换、召回、融合、重排与最终引用的阶段证据 | 测试语料、切片与改写策略、Embedding、向量库、全文检索、Reranker | step1、step9d、step10 |
| P02 企业知识接入 | 来源、版本、ACL、解析/索引作业状态、质量报告及变更/删除传播 | 文件解析器、同步器、存储、索引服务、表格处理算法 | step7、step9c、step16 |
| P03 RAG 评测 | 数据集和证据版本、逐样本结果、分组分析、检索/回答/引用指标及发布回归门禁 | 数据集内容、grader、具体指标计算和可视化目标 | step1、step10、step11 |
| P04 GraphRAG/KG | 图谱制品及来源/权限/版本记录，图检索作为可选 Knowledge 能力参与同条件比较 | 业务 Ontology、实体抽取/消歧、图数据库和 GraphRAG 算法 | step9d、step10；有场景再启用 |
| P05 工具调用可靠性 | 工具 schema、可信参数校验、动作身份/幂等/结果未知、错误分类和回执，工具选择错误可评测 | 工具目录内容、业务 API、选工具模型或排序器 | step3、step6—step7、step10 |
| P06 身份/权限/安全 | 人、Agent、服务和委派身份；工具、文档/行/派生索引的权限；高风险审批与攻击负例 | 企业 IdP、策略引擎、DLP、具体规则包 | step4、step7、step9c、step16 |
| P07 流程与人工介入 | 持久任务、等待/恢复能力声明、审批/接管、授权补偿动作，以及确定性流程调用 Agent 的关联契约 | 金融结算样例、业务状态机、特定补偿逻辑、planner | step3、step6、step13—step14 |
| P08 评测、观测与生产定位 | 可追溯事件与阶段诊断、失败分类、版本化回归、成本/延迟来源、范围明确的就绪证据 | Trace UI、评估器、报表及告警后端 | step6、step10—step11、step16 |

工作簿列出的测试语料规模、比较算法数量、失败样本数量是该实验的接受条件，不应写死成平台 API 或生产容量限制。平台需要允许按数据集、权限范围、知识/组件版本、实验配置和结果来源重复运行并导出证据。对缺少内部可见性的提供方，能力声明应返回 `unsupported/unverified`，不能用平台生成的推测 trace 补齐。

每项能力分别记录**架构角色**（治理契约/参考实现/集成/生态资产）、**成熟度**（实验/已验证/生产支持）和**部署形态**（同进程/独立进程/远程服务）。`Stable Core`、`Adapter`、`Labs` 不构成同一条晋级链：adapter 可以生产可用，核心的新契约也可能仍在实验。

### 组件与责任划分

这三张表按三个问题阅读：**表1**说明企业使用 Hecate 时必须得到哪些保证；**表2**列出可以接入来提供这些能力的服务/组件，以及替换时要满足的条件；**表3**区分平台管理的资产与管理资产的服务。表1最后一列是可选实现示例，不是与前两列一一绑定的产品清单。表中的“平台保证”表示 Hecate 定义验收契约，并在自身控制的入口执行，或验证受信任执行方返回的回执；不表示所有服务都由 Hecate 自研。

例如，企业可以用自己的身份系统登录，也可以接入第三方策略服务做授权决策；但真正调用工具前，仍需有受控执行点核对授权并记录动作结果。这样，身份和策略服务可以替换，企业的授权与审计保证仍然成立。

**表1：Hecate 必须保证的治理结果**

| 必须保证的结果 | 跨实现必须稳定的语义 | 可接入的实现示例（非必选） |
|---|---|---|
| 身份与授权上下文 | tenant、principal、代理主体、on-behalf-of、委派链和撤销状态可验证；默认拒绝、子授权收窄 | IdP、目录同步、身份映射、策略决策服务；Hecate 保留授权上下文绑定和执行点验证 |
| Agent 登记与部署治理 | 稳定 Agent/Deployment 标识、责任人、固定版本、能力快照、准入状态和撤销 | Agent/模型目录、部署控制器、配置中心、CMDB |
| Task/Run 控制 | 任务责任、运行关联、幂等、状态收敛、控制命令回执和未知状态对账 | 调度器、队列、持久化 worker、外部编排器；需明确单一状态写入方和字段所有权 |
| 团队与委派 | 成员资格、任务范围、交接包、资源 ACL、预算/深度/期限及验收关系 | 组织/团队目录、协作工作流和分派服务；平台仍执行委派授权及产物访问判定 |
| 动作策略与审批 | action 的主体、目标、参数摘要、策略版本、审批绑定和实际执行回执可关联 | Policy Decision Point、审批/ITSM 工作流、工具网关；必须有强制执行点和可核验回执 |
| 发布与质量门禁 | 不可变发布清单、门禁规则、评估证据与部署版本绑定，撤销可阻止新动作 | CI/CD、评估器、模型/制品仓库、发布控制器 |
| 治理证据 | 关键事件可关联、可追溯、受访问控制并满足保留/导出策略；不能依赖采样 trace | 审计库、对象存储、SIEM、日志/追踪分析平台；平台保留统一事件 envelope 和来源/完整性信息 |
| 生态准入与供应链 | 包含来源、版本、摘要、许可、权限、数据流、签名/扫描及符合性结果；可停用和追责 | 资产目录、包仓库、签名服务、扫描器、安装/升级服务 |

**表2：可替换的服务/组件类型及接入条件**

表1从“责任”角度组织；本表从“技术实现”角度组织，所以同一类服务可能支撑表1中的多项责任。例如 IdP 同时参与身份上下文和团队成员映射。

| 服务/组件类型 | 典型可替换项 | 替换时 Hecate 必须验证 |
|---|---|---|
| 执行与调度 | 内置/自托管 Runtime、供应商托管 Agent 服务、任务调度器、队列/worker、checkpoint 服务 | 对接平台 Task/Run/事件/控制契约；区分平台与供应商拥有的会话状态，声明暂停、取消、恢复等保证等级；不要求模仿 Pregel 内部结构 |
| 执行环境 | Sandbox 服务、容器/microVM 后端、浏览器环境、持久工作区 | 独立的环境生命周期、租户/Run 绑定、资源与出口限制、凭据作用域、终止回执和数据退出；环境恢复不等于任务恢复 |
| 上下文与数据 | Memory、完整 Knowledge/RAG 服务，或由可替换 Loader/Parser、Chunker、Embedding、VectorStore、Retriever、Reranker 等组成的 Knowledge 实现；artifact/object store | 分别核验整体服务契约和已启用内部组件的能力；明确租户与资源 ACL、来源/版本、索引更新与删除/迁移语义；不把外部存储的“可访问”视为“已授权”，也不要求黑盒服务暴露不存在的内部阶段 |
| 安全服务 | IdP、策略引擎、凭据库/KMS、沙箱、网络出口控制、DLP | 明确决策与执行边界；密钥只以引用传递；故障时执行既定 fail-closed 规则 |
| 质量与可观测 | Evaluator、trace/metrics/log backend、SIEM、告警系统 | 区分诊断数据和不可采样治理证据；评估结果绑定 evaluator 与数据集/规则版本 |
| 生命周期与生态服务 | Agent/adapter/Skill 目录、制品仓库、签名/扫描、CI/CD、安装器 | 允许企业既有系统作为实现；Hecate 依赖中立 manifest、准入结果和可撤销绑定，不要求另建完整市场 |
| 管理与交互 | 管理 UI、人工审批工作流、通知、工单系统 | 命令走统一 API/事件契约，返回 requested/acknowledged/applied 等真实状态；界面不是状态真源 |

语言和部署方式也是表2中的可替换维度：Python 内置实现、TypeScript Runtime、Go/Rust 网关、企业自托管服务或供应商托管 Agent 服务，只需满足对应能力的契约和治理验收，不需要移植为 Python 包，也不需要采用某个示例项目。跨语言组件以独立进程、容器或远程服务运行；同进程 Python 插件只是其中一种实现方式。网关是否选 Go/Rust，应依据实际吞吐、延迟、隔离需求和团队维护能力验证，不作为架构前提。

**表3：被管理的生态资产与管理它们的服务**

左列是被安装、绑定、运行和版本管理的内容；右列是负责查找、检查、分发和撤销这些内容的程序/平台。两者不是同一种东西，也不要求都由 Hecate 提供。

| 生态资产（内容/制品） | 管理资产的生命周期服务（可替换） |
|---|---|
| Agent/团队包、Skill、工具描述、连接器、行业模板 | 注册、解析、扫描、签名校验、版本兼容、安装、升级、撤销服务 |

举例：一个 Skill 包是资产；私有包仓库、漏洞扫描器和安装器是管理这个资产的服务。Hecate 可以提供默认实现，也应允许接入企业私有仓库或第三方目录，并以兼容 manifest、策略准入和符合性测试管理资产。外部策略服务的“允许”结果本身也不构成强制治理：动作必须经过 Hecate 或经验证的执行网关，且关键结果要有可关联回执。

“整体可替换”指实现可替换，不表示授权、证据和租户隔离可以跳过。每个能力项应分别记录责任保证、实现提供方、强制执行点和验收证据，避免用单一“核心/插件”标签混淆边界。

**跨语言接入规则：**平台先定义与语言无关的请求、响应、事件、错误、能力声明和版本规则，再由 Python/TypeScript/Go/Rust 等各自实现 adapter。第一个进程外参考接入采用 HTTP/JSON 与 OpenAPI/JSON Schema，事件通过游标查询或流式订阅，较大的产物传引用；其他传输（如 gRPC）在有实际需求时增加映射，不能改变 Task/Run、Action 和治理事件的语义。Python 类和 ORM 只是内置实现，不能成为第三方必须导入的契约。服务间身份、租户上下文、短期凭据、传输加密、幂等键、超时/重试和回执验证均属于接入验收。

### 各能力独立演进的边界

可替换、可独立演进、可独立消费分别验证。同进程 Python 插件可以替换算法或提供方，但通常仍要随宿主进程安装、重启和发布；需要独立选择语言、扩缩容、升级、故障隔离或安全边界的实现，采用独立进程、容器或远程服务。独立消费还要求调用方不必安装控制面或使用其数据库，能够从公开制品和契约完成安装、配置与启动。部署形态由能力需求决定，不要求现在把控制面拆成微服务。

| 演进维度 | 现在必须固定的规则 | 后续如何验证 |
|---|---|---|
| 契约所有权 | Runtime、Memory/Knowledge、Evaluation、Observability、Gateway 等由各自能力 owner 管理公开契约；共享的身份、Task/Run、Action 和证据语义保持最小集合。`contracts/` 可以同仓管理，但按能力独立版本与发布，不设一个全平台版本号强迫所有组件同步升级。 | 单独修改一个能力的实现或可选字段，不要求修改其他能力的 DTO、数据库或发布版本。 |
| 版本与绑定 | 区分契约版本、adapter 版本、服务实现版本、数据格式版本和能力认证结果。Deployment/ProviderBinding 固定已验证组合；Run 固定其实际使用版本及能力快照。 | 新旧 adapter/服务可短期并存；新 Task 按路由切换，既有 Run 继续使用原绑定；不兼容组合拒绝新绑定。 |
| 兼容与退出 | 定义字段扩展、未知字段、必填字段、错误码、事件及语义变更规则；破坏性变更通过新契约版本和迁移指南引入。每个版本有支持窗口、停用条件和负责方，不承诺任意历史版本永久兼容。 | 契约测试覆盖当前和仍受支持的版本；弃用前验证调用方、数据迁移与回滚。 |
| 状态与数据 | 每类服务指定自己的状态写入方、数据所有权、备份/导出/删除和迁移语义；控制面只保存治理所需引用和证据，不读取或改写后端私有状态。 | 只升级或替换 Memory 时，不迁移 Pregel checkpoint；只升级 Runtime 时，不重建 Memory 数据或平台 Task 记录。 |
| 扩展与差异 | 最低契约保持小而稳定；高级能力通过可协商的能力声明和命名空间扩展开放，未支持即显式拒绝。平台保证只覆盖已认证能力，不能为“统一接口”压平有价值的后端差异。 | 两个结构不同、至少一个非 Python 的实现通过共同治理场景；专属能力不污染最低契约，前端能显示实际能力。 |
| 故障与发布 | 每个进程外能力有独立健康、限流、超时、熔断/降级、部署和回滚策略；失败不应自动触发其他能力的重启、升级或无界重试。 | 单独重启/升级/回退一种能力，其他已绑定服务继续运行；失联与未知结果按各自契约处理。 |

这是一组独立发布和受控组合的目标，不是“所有版本与所有服务任意混搭”的承诺。Runtime 的独立消费已有车商 App 这一具名消费者，独立宿主在 step5 交付；Memory/Knowledge、评估、网关等按各自步骤验证独立契约与替换，不要求同时自建其完整独立产品。新增 Runtime ABC/Protocol 必须指出第二实现或具名消费者，优先复用已有扩展点，避免通用插件框架、跨服务事务或统一数据模型膨胀。

### 托管 Agent 服务与执行环境的独立选择

供应商托管 Agent 服务应作为一类正式执行后端，而不只是 A2A 对端或“远程黑盒”。以 OpenAI Agents API 为当前例子，供应商持有推理循环和会话；命令与文件所在的环境可由供应商托管、企业自托管，或根本不需要环境。其他供应商可能有不同组合，因此接入时分别登记 **harness/会话所有方**、**Sandbox/文件与命令所有方**、**工具与数据访问执行点**，不得仅用 `hosted` 或 `self-hosted` 标签推断整体数据边界。

| 执行组合 | 平台可以负责的部分 | 必须单独验证或明确不保证的部分 |
|---|---|---|
| Hecate 内置或企业自托管 harness + 企业环境 | 平台任务、部署准入、受控工具/数据入口、可验证的环境约束 | 后端是否真实执行暂停、取消和环境回收 |
| 供应商托管 harness + 企业环境 | 平台任务、环境准入与生命周期、企业动作网关及其回执 | 供应商会话、模型输入/输出、上下文保留与内部推理循环仍在供应商侧；环境自托管不等于会话私有化 |
| 供应商托管 harness + 供应商环境或无环境 | 平台提交与准入、可控工具/数据入口、平台直接观察的动作证据 | 供应商环境、网络出口、内部工具、数据保留与取消实际生效；不能依据供应商 trace 宣称平台强制控制 |

`Deployment` 固定两条轴及实际数据流、驻留地区、保留/删除条件、凭据与网络出口、控制等级和来源证据。数据分类策略先判断该组合是否允许使用；不满足私有部署、驻留或保留要求的供应商服务不得绑定相应任务。提供方条件会变化，具体限制应以准入时核验的服务版本、合同与文档为准。平台只承诺自己或经认证网关实际控制的动作；供应商可独立执行且无法封闭旁路的能力，应明确标为仅观察或不受控。

以本次调研时的[OpenAI Agents API 官方说明](https://developers.openai.com/api/docs/guides/agents-api/overview)为例，该服务仅支持美国数据驻留，且不支持 Zero Data Retention；选择自托管 Sandbox 也不会改变这两项限制。这是该服务当前的准入条件示例，不应写成所有托管服务的通用属性，后续选型时必须重新核验。

### 云端 Sandbox 与持久工作环境

Sandbox 与 Runtime、Memory 并列作为可替换且可独立发布的能力。Hecate 负责环境准入、授权绑定、资源预算、动作与生命周期证据；环境提供方负责创建、执行、隔离、回收及其底层基础设施。托管 harness 可以不使用 Sandbox，或连接企业/供应商环境；SandboxProvider 契约只适用于 Hecate 实际管理或可认证控制的环境，不能要求供应商开放其内部环境生命周期。先保留 Docker 参考实现，再接入一个实际云端或自托管环境服务；不以自建虚拟化调度平台为前提，也不设置 Docker → gVisor → Kata → Firecracker 的升级链。具体隔离组合按威胁模型和部署验收选择。

**必须分开的状态：**

| 对象 | 生命周期与所有权 | 不能混同的保证 |
|---|---|---|
| Sandbox 实例 | 提供方拥有实际资源状态，平台保存实例引用、Run/租户关联及已观察状态；执行命令与资源控制操作分别有 ID 和回执 | 请求终止不等于实例已销毁；失联不等于资源已释放 |
| 持久工作区 | 独立的数据所有者、ACL、配额、保留/删除策略；环境销毁后可按策略保留 | 工作区是文件资源，不是企业租户 workspace，也不自动构成长时记忆 |
| 环境模板 | 固定镜像/依赖摘要、来源、扫描结果及适用隔离 profile；通过发布准入 | 模板不能默认包含用户凭据、浏览器登录态或生产数据 |
| 运行快照 | 标明文件、内存、进程分别覆盖什么，绑定提供方、格式、权限及保留期；暂停/快照/fork 为可选能力 | 不承诺跨提供方还原；含凭据的内存或磁盘快照须按敏感数据处理，恢复后重做授权与凭据检查 |
| Runtime checkpoint / Memory / Artifact | 分别归 Runtime、记忆后端与产物服务管理；通过显式引用关联 Sandbox | 恢复环境不会撤销外部副作用，也不能直接把 Task 标为恢复或成功；已交付产物不能只存在临时环境 |

**接入方式：**语言中立的环境契约承载创建、查询、命令执行与结果查询、文件上传/下载、终止、租约/有效期及资源约束。暂停、恢复、快照、fork、浏览器/桌面及 GPU 等能力分别声明；不支持时明确拒绝。进程外契约传环境内路径或文件引用，不传宿主 `Path`、Docker 对象或 ORM。一个 Run 可以使用多个受授权环境；环境不得仅靠 agent_id 复用，复用键必须涵盖租户、信任范围和安全配置。

**交付节奏：**step3/7 定义最小契约并包装内置环境；step8 验证一个真实进程外后端及撤权/终止；step9/11 完成持久工作区与模板/快照治理；step13/14 支持授权交接和人类接管；step16/18 验证独立发布、成本与资源回收并发布符合性测试。先完成临时环境闭环，再根据长任务需求启用持久化和休眠能力。

### 基础能力域与未来组件边界

下面的名称是产品能力域，不要求现在创建七个服务或仓库。平台控制面的内部业务模块先在同一个进程和代码库交付；非 Python 后端或需要隔离的网关可从一开始独立部署。新增内部能力从第一天起放进有明确 owner 的子包；已有代码按步骤迁入或由薄适配层包住。一个现有顶层目录可以容纳多个能力域的**独立子包**，不能让它们共用业务服务、直接查询彼此的表或相互导入实现。下表描述规划覆盖范围，不表示能力已经实现。

| 能力域 | 本方案应覆盖的最小闭环 | 对应步骤与需补强之处 | 未来可拆出的组件职责 |
|---|---|---|---|
| Agent Engineering | Agent/Team 定义、配置与版本、可复现构建、调试、契约测试、发布清单及环境晋级 | step2—step5、step11、step18；明确开发到发布的制品与测试接口，不扩成通用 IDE | 工程工作台、制品/模板管理和构建集成；发布批准仍遵守治理门禁 |
| AgentOps | 运行健康、成本/配额、trace 与指标、告警、事件处置、故障对账、升级和容量管理 | step6、step10、step14—step16；补可操作的 SLO 与故障处置回路 | 运维监控、告警和事件响应服务；平台 Task/Run 状态仍有权威写入方 |
| Agent Control Plane | Agent/Deployment/Team 登记、Task/Run、分派委托、控制命令、人类接管、能力/后端绑定 | step3—step8、step11—step14；明确跨后端控制等级 | 登记、任务协调和管理 API；不承接 Runtime 内部 checkpoint |
| Agent Governance | 生命周期与责任人、策略协调、审批、发布门禁、证据保留、合规规则与审计导出 | step2、step7、step10—step11、step15；规则和证据由平台保证，可委托企业系统实现 | 策略/审批协调和治理证据服务；与执行点通过版本化决策/回执契约连接 |
| 安全 | Agent 身份、委派权限、凭据、工具/数据访问控制、沙箱与出口、敏感数据防泄漏、未知输入隔离 | step7—step8、step16—step17；补协议入口和外部内容的信任边界 | 身份/授权集成和受控动作网关；强制执行点不能只留在管理 UI |
| 评测 | 可复现数据集、离线回归、上线后抽样评估、人工复核、版本化指标与发布阈值 | step10—step11；补线上/线下评测区分及结果可比性 | 评测编排与结果适配；具体 evaluator、数据集和分析界面可替换 |
| MCP / A2A 企业接入层 | 入站与出站协议适配、发现与准入、身份/租户映射、权限、限流、数据范围、任务/工具调用映射与回执 | step1、step5、step7—step8、step17—step18；企业内部接入先于跨组织联邦验证 | MCP 工具/资源网关和 A2A Agent 网关可分别拆分；均复用平台身份、Task/Run、Action 和审计契约 |

这七类不是互斥的技术层：Control Plane 保存任务与控制状态；Governance 制定和追踪规则；安全在协议入口与动作边界执行规则；AgentOps 负责运行处置；评测产出质量证据供发布门禁使用；Engineering 管理开发到发布的制品；MCP/A2A 接入层只负责协议与信任映射。不得因未来拆分而在多个组件各建一套 Agent 身份、Task 状态或审批真源。

**当前就要落实的代码边界：**每个可拆候选单元指定一个内部包、对外应用接口、拥有的数据/状态及事件。跨包调用仅经公开接口和中立 DTO/事件；内置 Python 实现可在同进程直接调用并依赖注入，跨语言或隔离部署的实现通过该接口的进程外 adapter 接入，不预建所有能力的网络 RPC。ORM 可暂放在共享 `models/`，但每张表只能有一个领域负责读写；其他领域通过拥有者的服务查询，不能直接 import 其 repository 或写表。`core/composition/` 只装配实现。新增跨包依赖由分层检查阻止，历史依赖列出迁移清单并逐步收敛。

**未来拆分的实际工作：**当独立扩容、部署、安全隔离或维护团队需求成立，优先替换装配方式，将包的应用接口接到进程外 adapter；再迁移该包拥有的数据，并演练双版本兼容、切流和回滚。即使边界提前做好，数据搬迁、事务改造、延迟与故障处理仍需实施，不能承诺“零改代码”；目标是避免届时重写业务规则和所有调用方。

**提取验收：**对外接口和事件有版本、幂等键、错误与撤销语义及契约测试；包外没有实现类、repository 或私有表依赖；关键状态只有一个写入方；在单体内可用替代实现完成同一用例。满足这些条件后，再决定抽为独立包、进程或服务。七个能力域不必与七个部署组件一一对应。

### 本方案固定的设计约束

- 保留模块化单体作为控制面，不为“可插拔”而先拆微服务。跨语言运行时通过独立进程、容器或远程服务接入。
- 保留 Pregel 作为内置 Runtime；它的特性通过能力声明开放，不成为第三方必须模仿的内部结构。
- 受管模式下控制面拥有平台 Task 的责任/验收、Run 映射、授权和治理记录；执行方拥有实际执行事实、内部状态、checkpoint 和推理循环。独立模式由宿主拥有本地 Task/Run/Action 记录；接入平台后通过显式映射与事件投影关联，遵守第一节的字段所有权规则。
- 供应商托管 harness 的 session/turn 与平台 Task/Run 分别拥有状态；平台保存映射、游标与可验证证据，不复制或伪造供应商 checkpoint。供应商内部 subagent 不是自动注册的企业 Team 成员。
- 更换 Runtime 首先支持新任务切换；不承诺运行中状态无损迁移。上下文导出后重新执行必须明确记录为新的 Run。
- 企业人类负责人始终存在。Agent 的 persona、团队职责、系统权限分别建模。
- 所有运行绑定固定版本；执行点已知的紧急撤销和硬拒绝覆盖旧授权快照，不能用版本固定阻止撤权。受管断连无法保证实时收到中心撤销，必须依靠限时授权及过期拒绝约束陈旧窗口；需要即时撤权保证的动作必须在线判定。
- 不把 A2A、MCP 或某家 SDK 当成完整治理协议。协议适配与平台授权分别负责。
- 自治理限定为授权范围内的自动监测、隔离、降级和修复建议；Agent 不可批准自己的提权或修改自身审计要求。
- 观测可采样，关键治理证据不可依赖采样；“记录了 trace”不等于“动作已经被授权”。
- 只对经过验证的能力作承诺。外部 Agent 不能强制暂停时，API 和界面必须反映这个限制。
- 平台责任与服务实现解耦：Hecate 可交付参考实现，也可委托企业系统；每项关键治理语义必须指定执行点、权威状态写入方和可验证证据，禁止多套系统对同一状态无主双写。

## 二、现状及可复用资产

以下是架构调整的代码起点；以模块及职责定位。状态区分：**已合并**表示存在代码或文档交付；**部分完成**表示尚有启用、覆盖或语义缺口；**待实现**表示目标闭环尚未成立。已合并不自动等于生产认证，OpenSpec tasks 勾选也不替代实际调用链和验收证据。

| 当前资产 | 已有价值 | 本次调整方式 |
|---|---|---|
| [RuntimePort](../../src/hecate/runtime/ports.py) | 引擎调用模型、工具、知识、checkpoint 等外部服务的接口 | 保留其现有方向；不要直接改造成平台调用 Runtime 的接口 |
| [WorkflowExecutionService](../../src/hecate/studio/workflows/execution_service.py) | 组装图、Worker、guardrail 并执行 Pregel | 包装为内置执行后端，再逐步迁移其平台职责 |
| [AgentExecutionPort](../../src/hecate/core/composition/agent_execution_port.py) | 已从 runtime 移至 composition，承接具体平台服务 | 不再重复执行移出 runtime 的工作；其平台职责在 step5/7 向对应领域收敛，composition 不长期承载业务规则 |
| [PregelRuntime](../../src/hecate/runtime/pregel.py) | 图执行、暂停、恢复、事件与状态管理 | 继续维护；不再要求所有外部 Agent 转换成其 Graph DSL |
| [AgentModel](../../src/hecate/models/agent.py)、[AgentVersion](../../src/hecate/models/agent_version.py) | Agent 配置和不可变版本 | 增量补充身份、部署、后端绑定，不另造重复版本体系 |
| [MemoryProvider](../../src/hecate/core/composition/memory_provider.py) | entry point、能力声明、读写及生命周期契约 | 扩展绑定和治理，拆出与内置算法有关的策略 |
| [MemoryProvider 规格](../../openspec/specs/memory-provider-contract/spec.md) | 第三方接管记忆能力的已有设计 | 兼容升级，不从零重写 |
| [hecate-memory RAG VectorStore](../../packages/hecate-memory/src/hecate_memory/rag/vector_store.py)、[EmbeddingService](../../packages/hecate-memory/src/hecate_memory/rag/embedding.py)、[Citation](../../packages/hecate-memory/src/hecate_memory/rag/types.py) | 已有多个向量库 adapter 和向量接口；Embedding 默认实现、Citation 描述仍带具体实现假设 | 复用已有接口，验收替换、索引兼容和 ACL/删除语义；对外 Knowledge 契约不得沿用 Qdrant 对象、固定向量维度或默认 Embedding 名称作为规范字段 |
| [hecate-sandbox](../../packages/hecate-sandbox/README.md)、[AgentEnvironment](../../packages/hecate-sandbox/src/hecate_sandbox/environment/environment.py)、[EnvironmentManager](../../packages/hecate-sandbox/src/hecate_sandbox/environment/manager.py) | Docker 执行、环境管理、预热池及浏览器会话 | 将 local/docker 分支与本地 Path/全局配置依赖包在 adapter 内；新增进程外环境契约及提供方登记。LocalEnvironment 仅用于开发测试，不继承云端隔离承诺 |
| [工具 Gateway](../../src/hecate/tools/gateway)、[策略流水线](../../src/hecate/tools/policy) | 工具聚合、授权与调用边界 | 演进为跨 Runtime 的受控动作入口 |
| [审批组装](../../src/hecate/runtime/security/guardrail_assembly.py)、[审批模型](../../src/hecate/models/approval.py) | 内置执行中的安全控制 | 分离平台审批记录和 Runtime 暂停实现 |
| [TaskAllocator](../../src/hecate/runtime/task_allocator.py)、[EventBus](../../src/hecate/runtime/eventbus.py) | 执行内的选人和消息协作 | 保留为策略/执行内组件；平台级任务责任和持久化调度另设边界 |
| [A2A](../../src/hecate/channel/a2a) | 异构 Agent 通信和任务适配 | 纳入通用执行后端和组织间交换策略，不直接成为平台唯一状态源 |
| [评估模块](../../src/hecate/ops/evaluation)、[hecate-ops](../../packages/hecate-ops/README.md) | 评估、观测和 OTLP 基础 | 保留统一结果及治理门禁，支持替换执行和分析后端 |
| [分层测试](../../tests/test_layering_domain.py) | 依赖边界保护 | 扩展到新增领域，并补实际替换场景测试 |
| [功能目录](../features/feature-catalog.md)、[路线图](../features/roadmap.md) | 已有 Feature ID 和交付历史 | 保留 ID，重排归属、依赖和里程碑，不把历史实现抹掉 |
| [结构化功能清单](../features/feature-inventory.yaml)、[校验脚本](../../scripts/feature_inventory.py) | 已有 schema、依赖/证据/成熟度字段和 CI 校验入口 | 增量迁移字段所有权，保护已填写的治理数据，补生成与漂移检查；不从零重建 |

**独立消费增量核对：**已有 `tests/test_runtime/test_runtime_self_sufficiency.py` 主要证明导入隔离，不能替代发行包干净安装与端到端执行。`WorkflowExecutionService` 和 `core/composition/agent_execution_port.py` 仍涉及平台定义/ORM 与生产装配，`pyproject.toml` 尚未提供独立 Runtime 发行包。step1 增补实际依赖闭包与延迟导入清单，step5 将共享执行装配与平台定义查询分开；以上为静态核对，不是独立运行测试通过的结论。

### 前轮建议的合并核对与处置

下面的编号对应前轮修改建议，不新增 Feature ID。后续 change 应引用本表，避免把已完成修复重复排入重构。

| 前轮建议 | 最新基线事实与证据 | 定稿处置与剩余工作 |
|---|---|---|
| 1、2、3、5：管理权限与租户身份 | #174/#175；[backup](../../src/hecate/ops/api/backup.py)、[AuthContext](../../src/hecate/core/auth_context.py)、[workspace 依赖](../../src/hecate/core/deps_workspace.py)、[JWT provider](../../src/hecate/enterprise/auth/jwt_provider.py) 已补管理权限、移除空 workspace 自动提权、校验存续成员关系；模型提供方变更受管理员限制 | 已合并，作为回归基线保留。仍须覆盖各协议的资源级授权，不宣称全平台授权审计已结束 |
| 4：MCP 独立认证与隔离 | #175；[认证中间件](../../src/hecate/tools/mcp/auth_middleware.py) 与 [MCP server](../../src/hecate/tools/mcp/server.py) 已接入服务端身份和租户过滤 | 部分完成：认证不能代替写操作角色检查和受控工具执行，见 G1 |
| 6、8：审计身份与 A2A 签名 | #176；请求绑定 auth_context；[A2A discovery](../../src/hecate/channel/a2a/client/discovery.py) 在开启签名校验时对无签名/不可信密钥失败关闭 | 已合并特定修复。是否强制校验由准入 profile 决定；签名不能代替资源授权，平台证据模型仍在 step6/7 |
| 7：LLM 配置和费用 | #176；[Runtime adapter](../../src/hecate/core/composition/runtime_port_adapter.py) 已透传调用参数并优先用 provider token usage，缺失时估算 | 部分完成：固定 token 单价、估算来源和中断结算仍有缺口，见 G4；不得标为真实成本闭环 |
| 9：聊天执行收敛 | #178；[chat](../../src/hecate/channel/api/v1/chat.py) 已有引擎路径；`CHAT_TOOL_LOOP_ENGINE_ENABLED` 默认仍为 false | 部分完成：保留并验证现有路径，按入口灰度，不重新实现一套；G3 通过后才清理旧循环 |
| 10、11：Runtime 边界与替换测试 | #180；AgentExecutionPort 已迁出；[runtime 分层测试](../../tests/test_runtime/test_layering.py) 加强，已有 EventStore/Memory/LLM 契约测试 | 已完成边界修复；Stub/假后端证明接口可用，不等于真实异构 Runtime、Memory 或跨语言独立发布，后者交 step3/8/9 |
| 12：工具回执与 Temporal | #177；[ToolWorker](../../src/hecate/runtime/workers/tool_worker.py) 已有 execution_id、结果状态和副作用分类；[Temporal worker pool](../../src/hecate/runtime/temporal/worker_pool.py) 未实现路径显式失败 | 部分完成：Temporal fail-fast 是正确降级，不是已实现分布式执行；回执恢复仍有 G2，不能作为可靠重试完成证据 |
| 13、14、15：功能清单与规划收敛 | #181；YAML 清单、校验脚本、research 状态和路线图 Next Phase 已加入 | 部分完成：沿用成果，修复 G5；原 roadmap 的旧 Sprint/时间承诺与引擎中心定位仍需整体对齐，不能只在末尾追加路线 |
| 16、17、18：部署组合、重复产品边界、知识治理 | 当前仍有多个可选包与默认依赖，尚无本方案的统一 binding/退出验收 | 保留已有交付事实；按 step9/10/16/19 做 ACL/删除传播、参考部署和依赖收敛，不把移入 research 当作代码退役 |
| 19、20：发布负例、恢复门禁与示范闭环 | 已增加定向测试；尚未提供跨入口、跨后端、重启和发布组合的完整证据 | 在每个迭代建立技术验收场景，业务场景待真实需求出现后补齐；不以缺少客户为基础重构的阻塞条件 |

### 实施门槛及基线处置

以下保留前轮代码路径复核的原始问题及关闭标准，不是本次直接修改代码的清单。step1 已记录 G1/G2 分别经 #185/#186 修复，后续应保留回归并核对新路径，不重开同一修复；G3—G5 按关联 change 的最新证据确认。本地补丁关闭不等于外部后端或独立宿主自动取得同等认证。未关闭项先做最小复现和失败测试，只阻断受影响能力的启用，不阻断文档对齐、契约探索或隔离测试。

| 门槛 | 当前具体缺口 | 需执行的调整 | 关闭证据与责任步骤 |
|---|---|---|---|
| G1：统一动作授权 | MCP 的 `agent_create/update/delete` 等主要检查身份/workspace；`tool_execute` 自行构造 BuiltInToolExecutor，使用全局 WORKSPACE_ROOT，调用 [ToolRegistry.execute](../../src/hecate/tools/tool/registry.py) 未传服务端上下文，Registry 本身不执行统一策略/审批。Gateway 可选中间件不能作为所有配置下的保证 | step1 先复用现有权限/策略服务补角色检查和可信主体、租户、资源根；无法安全授权的工具显式拒绝，生产 profile 禁止无认证模式；梳理共享种子资源权限。step7 再归并到完整 Action 应用服务，不要求 step1 提前建成整个控制面 | viewer 写入、跨租户文件/工具、未批准写操作在 REST/MCP/Workflow 的实际入口均拒绝；先在 step1 关闭现有旁路，step7 推广到外部后端 |
| G2：不确定副作用与真实恢复 | `get_tool_receipt` 只找 TOOL_RESULT，读取失败返回 None；调用方只在 prior_status 非空时阻止重试。已有 TOOL_CALL 但结果缺失、存储不可读时可能再次执行。成功恢复仅返回占位文本，回执没有真实结果或产物引用；同 session/tool_call_id 的参数变化和并发领取也未构成完整保护 | 区分 never_started、claimed、outcome_unknown、store_unavailable；执行前持久化意图并原子领取，动作键绑定租户/Run/工具/参数摘要，冲突拒绝；结果存受 ACL 保护的引用；执行后异常不能仅凭异常类型判定无副作用；只能在明确幂等保证或已对账后重试 | 注入“写入成功→结果落盘前崩溃”、读库失败、重复 worker、同键不同参数，证明无盲目重复写；恢复得到真实结果。step1 先补安全停止语义，step6/7 完成持久化 Action 闭环 |
| G3：入口与引擎收敛 | 引擎聊天尚未默认启用；[chat convergence 测试](../../tests/test_runtime/test_chat_engine_convergence.py) 部分直接调用 ToolWorker 或 mock Workflow 服务，不能证明完整 HTTP/SSE 多轮执行及恢复一致 | 复用现有开关；补真实入口至引擎的流式/非流式、多轮、审批拒绝、断线/恢复和取消测试；检查拒绝记录传递及 tool call/result 配对；按 workspace 切流，保留活跃 Run 路由 | step5 每迁移一个入口提供端到端结果；G2 关闭前不宣称可恢复生产写入；step19 有旧路径无调用证据后删除 |
| G4：可解释用量与预算 | `_COST_PER_TOKEN` 仍为统一常量；估算未完整覆盖工具 schema/结构化调用；估算标记主要在日志，中断流未形成可靠结算链 | 用量记录区分 reported/estimated/reconciled、模型及价格版本、token 类别与来源；调用 ID 去重，异常/取消可记未知账，预留/结算/对账分离；无法获知托管消费时只限本地授权预算，不承诺硬限制供应商账单 | step7/10 用工具调用、provider usage 缺失、断流和迟到修订验证，不把未知当零；规则和样例由 step1 固定 |
| G5：规划数据的权威来源 | `extract` 全量重建 YAML，会覆盖手填 maturity/dependencies/evidence/acceptance；`check` 比对 ID 集合，非严格模式允许缺证据；并非已实现文档生成 | 先保护存量数据，改为按 ID 合并或仅首次导入；明确字段所有权；再从 YAML 生成 catalog/roadmap 的受管区域并检测漂移。对本轮新增/变更或宣称 production 的条目强制验收字段，历史空缺登记补齐计划 | step2 保存提取前后元数据不丢失、冲突失败、生成幂等及状态一致测试；不得直接再运行当前 extract 覆盖已有清单 |

G1/G2 是受保护写操作、自动恢复和外部执行接入的前置门槛；G3 是切换默认聊天路径的门槛；G4 是发布成本保证的门槛；G5 是依赖路线图自动化的门槛。新架构不能用“以后会统一”作为保留现有危险旁路的理由。

### 前轮验证记录（历史证据）

以下为前轮代码和文档复核、定向测试及功能清单校验的历史记录；不等于本次重跑、全面安全审计、生产压测或真实第三方接入认证。G2 复现描述修复前状态，修复后处置见 step1。

- 使用本地 `.venv` Python 3.14.6 运行 tool receipts、chat convergence、RuntimePort cost、A2A signing、domain layering、旧 runtime layering 测试：44 passed、1 skipped。旧 layering 用例仍指向已不存在的 `src/hecate/engine/`，跳过不能当作分层通过；当前边界另由 domain layering 和 runtime self-sufficiency 验证。出现 pytest 缓存写入 warning，不影响该组断言结果。本次不是 Python 3.12 或完整 CI 矩阵认证。
- 补跑 `test_runtime_self_sufficiency.py`、`test_backup_api.py`、`test_tenant_isolation.py`：21 passed；用 `-p no:cacheprovider` 避免本机缓存目录 warning。该结果支持具体边界修复，不涵盖 G1 所述全部协议授权路径。
- 补跑 A2A discovery verification、audit identity：9 passed，1 项在临时目录 fixture 阶段因沙箱文件权限失败；使用新临时目录并经工具批准在沙箱外单独重跑该项，1 passed。不是产品断言失败。各组最终合计 75 passed、1 skipped，没有运行全量测试、生产数据库故障测试或真实供应商调用。
- `python scripts/feature_inventory.py check`：347 条目、0 errors、2 warnings；333 项缺 evidence，333 项缺 acceptance。结构校验通过不能证明所有能力已通过验收。
- G2 最小复现：复用 `test_tool_receipts.py` 的 StubPort/ToolWorker，同 session/call_id 连续执行同一未知写工具；正常 EventStore 仅调用一次，但恢复返回占位文本。将 `get_events` 过滤为保留 TOOL_CALL、缺失 TOOL_RESULT 后，StubPort 被调用两次。仅在内存和 Stub 中执行，无外部副作用；这证明缺失结果的恢复分支尚不能安全阻止重复写入，不代表已经模拟所有崩溃窗口。

## 三、目标模块与依赖方向

下列为建议的新逻辑边界，路径尚未全部存在。step2 固定职责，step3/step8 验证后再确定稳定接口，不以提前建空目录作为完成标准。

| 建议边界 | 职责 | 明确不承担 |
|---|---|---|
| `contracts/`，按能力独立发布 schema/类型包 | 跨后端 DTO、事件 envelope、能力声明、协议版本；step3 提供进程外产物，step5 解除独立包对主应用类型的依赖 | SQLAlchemy、FastAPI、业务服务和供应商 SDK |
| `execution/` | 后端登记与解析、平台 Task 责任/验收、Run 映射与投影、事件归一化、控制请求 | 改写后端执行事实、Agent 内部推理循环和各引擎 checkpoint 格式 |
| `collaboration/` | Team、Assignment、Delegation、交接、验收、人工升级 | 所有外部消息协议的实现、重新实现 Pregel |
| `enterprise/` | 身份、权限、策略协调、凭据和组织信任 | 供应商专属运行逻辑 |
| `tools/` | 工具与数据资源接入、受控动作执行 | 决定谁是企业平台管理员 |
| `runtime/` | 内置 Pregel 和内部扩展点 | 平台团队目录和外部 Runtime 管理 |
| 独立执行宿主（建议 `packages/hecate-runner/`） | 本地定义加载、共享执行装配、身份/策略接入、持久化与证据 adapter、服务入口；可选择连接控制面 | 复制 Runtime、使用平台管理表启动、车商业务逻辑、强制装载全部后端 |
| `ops/` | 治理证据、评估结果和事件处置；向 Governance 提供发布所需证据 | 拥有发布批准真源、强制所有 trace/评分都存在内置系统 |
| `studio/` | Agent Engineering 的配置/管理界面与人类协作界面 | 直接修改执行、授权或发布状态真源 |
| `channel/` | REST、MCP、A2A、IM 等入站/出站协议适配；完成协议身份映射后调用平台服务 | 自建 Task/Run 状态或把协议身份直接当作平台授权 |
| `core/composition/` | 各实现的装配和生命周期 | Task 调度、业务权限等新的业务逻辑 |

现有顶层目录是组织代码的容器，不自动成为未来的组件。建议按下列子包逐步落地；新目录只在相应步骤需要代码时创建，既有文件按依赖清单渐进迁移。

| 可拆候选单元 | 单体内的目标代码边界 | 状态/调用所有权 |
|---|---|---|
| Agent Engineering | `studio/engineering/`，封装现有配置、模板与构建/测试入口 | 拥有草稿和构建记录；通过发布接口提交制品，不直接改发布准入状态 |
| AgentOps | `ops/agentops/`，聚合健康、告警、事件处置 | 拥有告警与处置记录；Task/Run 状态从 `execution/` 查询 |
| Control Plane | `execution/` 和 `collaboration/` 两个内聚包 | 前者拥有 Deployment 期望配置、平台 Task 责任/验收、Run 投影和控制请求，实际执行状态来自后端；后者拥有 Team、Assignment、Delegation 与协作验收记录；跨包用应用接口 |
| Governance | `enterprise/governance/` 与按需建立的 `ops/evidence/` | 前者拥有策略/审批/发布决策；后者拥有治理证据写入与查询；两者以事件和证据引用关联 |
| 安全执行 | 现有 `enterprise/auth/`、`enterprise/vault/`、`tools/gateway/`、`tools/policy/` 各自保持独立 | 身份解析、凭据和动作执行分别有 owner；经版本化授权请求/决策/执行回执协作，不强行合并成一个包 |
| 评测 | 现有 `ops/evaluation/`，外部 evaluator 放在其 adapter 子包 | 拥有评测任务与结果；Governance 只消费结果和证据引用 |
| MCP / A2A 接入 | 现有 `tools/mcp/`、`channel/a2a/` 及各自的 gateway adapter | 拥有协议会话/映射；Task/Run、身份授权和 Action 状态由对应平台包拥有 |

区分**运行调用链**与**源码依赖方向**。运行时为协议/界面入口 → 领域应用服务 → 注入的 adapter → 后端；中立契约不是一个转发服务。源码上，领域服务和 adapter 各自依赖中立契约，adapter 可依赖供应商 SDK，领域服务不得反向导入具体 adapter；`core/composition/` 负责将它们装配起来。`contracts` 不依赖领域、ORM、Web 框架或供应商 SDK。同进程调用公开接口，不通过共享数据库绕过接口。遵守已有命名规则，不将所有接口都命名为 `XxxPort`。

物理部署分别起步：独立模式为执行宿主 + 所选模型/业务 adapter + profile 所需存储；平台模式为模块化控制面 + 独立 worker/执行宿主 + 所需管理存储及后端。管理数据库和执行数据库分别归属；初期可用同一 PostgreSQL 实例的隔离 schema/账号，但禁止跨 owner 直接读写或共享事务，独立宿主不能要求预建平台管理表。生产持久化先复用已验证的 PostgreSQL 能力，不把未经验证的 SQLite 等适配当作默认生产承诺。

源码依赖目标：业务 App/协议入口 → 宿主应用接口 → Runtime 公开接口；宿主装配本地或远程 adapter，Runtime 不反向导入宿主/控制面。平台 adapter 可调用宿主或共享装配，不再复制执行流程。建议 `hecate-runtime` 承载内核及执行语义，`hecate-runner` 承载宿主；名称在 step2 固定，目录迁移随 step5 拆分完成，旧导入在有明确期限的薄兼容层中保留。独立发行包不能靠依赖整个 `hecate` 包、可编辑安装或仓库源码路径实现“独立”。

## 四、执行总表与依赖

| 步骤 | 建议 change 名称 | 直接前置 | 主要交付 |
|---|---|---|---|
| step1 | `platform-evolution-baseline`；增量 `standalone-consumption-baseline` | 无；保留已完成快照 | 已修复项回归引用、独立运行依赖/部署清单、App fixture 规格与能力差距 |
| step2 | `governance-platform-positioning` | step1 的清单与边界结论；可与修复并行 | 定位、ADR、现有 YAML 增量治理、路线图 |
| step3 | `execution-backend-contract` | step2 的边界决策 | 执行/本地制品草案契约、能力模型、非 Python 真实后端窄范围验证 |
| step4 | `agent-deployment-task-model` | step3 | 身份关联、Deployment、Task、Run 数据模型 |
| step5 | `builtin-execution-backend`，拆分见 step5a—5d | step3；平台入口映射依赖 step4，独立装配不依赖平台表 | 共享装配、独立发行包/宿主、本地只读技术预览、入口兼容 |
| step6 | `durable-task-control-plane`，本地持久化先行 | step5 的执行切片；平台投影另需 step4 | 本地持久任务/Action、平台任务映射、控制命令与治理事件 |
| step7 | `runtime-independent-governance`，本地与受管分别验收 | step6 相应状态与回执；安全基线完成 | 本地业务身份/策略、受管授权租约、工具/审批/凭据/预算与断连边界 |
| step8 | `external-execution-backend-pilot` | step7；复用 step3 验证 | 分别认证异构自托管、托管 harness、远程 Sandbox；未通过组合不影响已认证组合 |
| step9 | `memory-knowledge-provider-binding` | step7 身份/资源授权；平台绑定另需 step4，本地绑定沿用 step3/5；知识诊断可先用合成数据 | 分别交付 Memory（9a/9b）与 Knowledge（9c/9d）：整体服务替换、组件化检索、知识接入生命周期、权限/删除与索引兼容；可与 step8 并行 |
| step10 | `evaluation-observability-backends` | step6 的事件和版本关联稳定；Knowledge 专项评测需 step9 的样本/结果契约 | 外部观测/评估适配、逐样本与分组实验、阶段诊断；最小评测基线和证据从 step1/6 开始 |
| step11 | `governed-agent-release` | step3/5 制品、step7 相应治理、最小评测证据；中心发布另需 step4，外部部署另需 step8 | 先完成独立 builtin 的本地准入/升级，再接中心发布及组合门禁；不等待 step9/10 所有后端完成 |
| step12 | `enterprise-agent-teams` | step11 | 团队、成员、职责和资源共享 |
| step13 | `durable-team-coordination` | step12 | 委派、任务调度、产物交接和验收 |
| step14 | `human-team-supervision` | 最小干预在 step6/7 即交付；完整团队视图依赖 step13 | 本地 App/平台最小干预接口、团队工作台与真实回执 |
| step15 | `bounded-autonomous-governance` | step11、step14 及处置动作的可靠性证据 | 有边界的自动处置；不阻塞人工运维的生产支持 |
| step16 | `platform-production-conformance` | 随 step5—step14 分段验证；按待发布范围收口 | 恢复、隔离、负载、部署及升级支持矩阵，不等到最后才测试 |
| step17 | `cross-organization-agent-federation` | step16；明确跨组织场景 | 组织间信任、数据交换、远程任务对账 |
| step18 | `adapter-ecosystem-distribution` | step16；已有外部贡献需求；基础接入产物在 step3/5 先交付 | 扩展多语言 SDK、认证适配器、内部资产目录与供应链分发 |
| step19 | `legacy-platform-consolidation` | step16；相关新路径通过验收 | 旧路径退役、可选包整理、最终文档收敛 |

step11 拆为最小发布门禁和多能力组合发布：前者在 step7 后服务 builtin 及已认证后端，后者随 step8/9/10 增加组合。最小绑定记录只记录已使用的实现、配置摘要与版本，不依赖第三方 Memory 迁移完成。step16 是贯穿式质量工作包，生产支持按范围授予；step15、step17、step18 不阻塞基础平台的生产支持。step19 分批执行，不等待所有生态能力完成，也不一次性删除全部旧代码。

**独立产品最短交付路径：**step1 增量 → step2 边界 → step3 本地制品/调用契约 → step5a/5b/5c → step6 本地可靠执行 → step7 本地授权/审批 → 最小 step10/11 + 对应 step16。到此可交付限定范围的独立生产 profile。step4 与 step5d 提供平台映射，step6/7 的受管切片完成后再连接控制面；独立交付不以组织/团队、知识库、全部平台入口迁移或完整生态建成为前置。实际启用的可选能力须额外完成其所属 step 与符合性测试。

## 五、逐步实施清单

### step1 — 固定实施基线与验证场景

**目标：**固定当前代码、执行链路与依赖基线，建立平台治理和组件独立消费的可重复验证场景；明确当前能力与目标能力之间的差距，不把旧问题、在途修复和架构调整混在一起。

**操作：**

- [x] 记录当前目标分支与基线提交，检查 `git status --short`、`openspec list --json`，列出在途 change、负责人和影响模块。（基线 §1；基线提交 `b8fd926`，当时在途 change 为空）
- [x] 核对 #174—#182 与前轮建议，建立第二节的已合并/部分完成/待实现快照；不再等待已合并的 auth-boundary-hardening。
- [x] 为 G1/G2 建立失败复现并单独修复；为 G3/G4/G5 记录启用门槛、owner 和目标 change。认证已修复不代表所有资源操作都授权正确。（G1/G2 初次修复已由 #185/#186 合并；复核补充修复见 `step1-review-hardening`，覆盖 MCP 文件写入角色、Postgres 锁与版本分配、工具身份及未决写恢复，待合入；G3—G5 门槛、复现指针与目标 change 记录于基线 §7，owner 指派暂缓（用户 2026-09-28 决定），对应 change 启动时指定）
- [x] 建立执行入口清单：Agent chat、普通工具 chat、Workflow、MCP、A2A、定时任务、IM、评估调用。逐一记录实际执行服务、安全入口、事件存储和取消方式。（基线 §2，含入口层缺口 N1—N5）
- [x] 建立后端清单：Runtime、Memory、Knowledge、Evaluation、Observability、Sandbox；标明哪些接口已存在、哪些只有内置实现。（基线 §3）
- [x] 为每种部署形态绘制信任与数据流拓扑：人类客户端、控制面、Runtime、工具/MCP/A2A 网关、Memory、凭据代理、外部系统及网络出口；列出每条可能绕过授权或审计的直连路径、当前阻断机制与待验证负例。（基线 §4：B1—B5 直连路径带代码证据与当前阻断；部署层出口控制标注未核验）
- [ ] 为托管执行组合补 harness 会话、Sandbox、远程 MCP、应用回调和供应商内部工具的数据流；区分供应商可见的数据、企业实际持有的环境、可由平台强制的动作。记录当前驻留、保留/删除和服务可用地区限制，作为接入门禁输入。（登记格式与已核验示例见基线 §5；实际组合数据流随 step4 Deployment 模型落地）
- [x] 按第六节“已实现能力的边界复核清单”核对现有代码、启用状态、安装依赖、API/数据使用量及负责人；分别记录已是可选包、已拆包但仍为核心依赖、完全耦合在主应用中的情况。（基线 §11：十项能力逐项给出打包分类、依赖与挂载证据；运行时使用量标注【未核验】，owner 待指定）
- [x] 为七个能力域建立现有代码/数据库归属清单：文件与表的 owner、跨包 import、直接写表、API 调用和事件流；标出未来拆分会触及的共享事务与全局状态，作为逐步迁移基线。（基线 §6：跨域读写样本、共享事务面与全局状态已登记，TODO-O1 关闭；反射式写入等动态路径标注未核验）
- [x] 建立不依赖真实企业系统的验收样例：读取材料、产生摘要、独立复核、人工批准、向测试工单服务写入结果。（场景包 S11 全链；S01/S02/S03 覆盖分段）
- [x] 固定架构评测包：输入材料及 ACL、测试工具和初始状态、预期产物 schema、禁止动作、审批人、故障注入点与环境复位。权限/副作用用确定性断言；内容质量使用明确 rubric 并记录 evaluator 版本，不能只判断 Agent 自称完成。（基线 §9 规格冻结 + `tests/scenarios/` 实现，manifest 一致性测试钉住漂移）
- [x] 建立 `Problem_Lab.xlsx` P01—P08 到平台保证、可选实现、责任 step、验收 fixture 与未支持能力的映射；先以少量带版本、权限和标准引用位置的合成文档及测试工具贯通，不把表格中的固定样本规模和金融案例变成平台最低要求。（基线 §8 P 组摘要 + `tests/scenarios/manifest.yaml` 场景级唯一事实源）
- [ ] 对同一评测包保留单 Agent 的成本/结果基线，再比较多 Agent；每次变更运行受影响场景。安全负例、重复写入和跨租户泄露不允许以平均质量分抵消。业务收益暂记未验证。（结果基线与成本采集口径已固定（Tier-2 记录 cost_baseline 块）；真实成本基线受 G4 门槛（目标 step7/10），多 Agent 比较随团队协作模型（step12/13）启用）
- [x] 保存旧路径的响应协议、任务结果、权限负例和事件样本，作为迁移比较基线；模型回答比较业务结果，不比较随机文本逐字相等。（场景包 S10 黄金样本 + S02/S05—S08 负例与恢复样本）
- [x] 将旧分支已有失败调查归档到基线记录，不能直接标成“与本次无关”。（基线 §10）

**独立消费增量（新 change，不重做上面的已完成项）：**

- [x] 重新记录此次实施所用提交、在途 change 和包版本；历史 `platform-evolution-baseline.md` 保持快照性质，新增 `docs/refactor/standalone-consumption-baseline.md` 并链接原证据，不覆盖原结论。（独立基线 §1；基线提交 `a4851bb`，在途 change 为本 change 与 `openspec-spec-hygiene`；仅静态核对，无安装/运行验证）
- [x] 从实际生产执行链梳理定义加载、图编译、Worker、身份/策略、模型/工具、checkpoint、证据和启动配置的依赖闭包；包括 `runtime/AGENTS.md` 的函数内 import。逐项写明代码位置、是否执行必需、可由宿主注入/可选安装/待解除耦合及责任 step。核对 wheel 依赖，不以 import 探针通过代替可独立安装。（独立基线 §2 八链段闭包表 + 懒加载清单逐行归类，§3 wheel 交叉核对；全部为静态核对，安装性验证归 step5b）
- [x] 为独立、受管、完整私有平台模式补拓扑；另记网络模式、可信身份来源、任务/执行状态 owner、凭据和数据出口。受管断连明确授权有效期及重新连接的责任方，不能沿用在线撤权保证。（独立基线 §4；断连授权有效期、最大陈旧窗口与重连责任方登记为目标语义，待 step6/7 落地）
- [x] 在现有 `tests/scenarios/manifest.yaml` 追加独立消费场景组（建议前缀 `SC`，实施时检查未占用），保留 S/P 组 ID。提供模拟库存 API、两个隔离的数据域、只读身份与需审批的测试写动作；业务规则放 fixture，不新增 Hecate 车商领域模型，不引入 RAG 前置。（manifest `sc_scenarios` SC01—SC10 全部 `planned` 并绑定责任 step 与门禁 change，S/P 组零改动；fixture `tests/scenarios/tools/inventory_api.py` + `test_sc_fixture_inventory.py` 仅验证 stub 自身行为，不声称宿主能力）
- [x] 固定验收规格：干净安装且无管理平台冷启动、读取/生成建议、无权调用拒绝、持久任务重启、受保护写入未知结果、断连授权过期、审计存储失败、重连重复命令及数据外发检查。记录当前支持/未支持/未验证及责任 step；尚未实现的能力不伪造通过报告，也不以永久 skip 当完成。（独立基线 §5 登记表 SC01—SC10 当前状态均为未支持，与 manifest 由 `test_manifest_consistency.py` 钉住一致；SC 实现测试随责任 change 交付后翻转）
- [x] 建立差距表：step5 负责安装和只读执行，step6/7 负责持久化/本地强制策略和受管断连，step10/11 负责证据与制品门禁，step16 负责组合认证。每项挂实现 owner、验收 owner 的待指派字段和指派时点，不虚构人员或工期。（独立基线 §6 step5a—step16 差距行，owner 待指派、指派时点为 change 启动时；条件性延期项另见 §7）

**落点与交付：**沿用既有 `docs/refactor/platform-evolution-baseline.md` 与场景包；独立消费增量写入单独基线文档及 manifest/fixture。step1 交付现状证据与可重复输入、预期断言，不要求目标 Runtime 已经独立运行；实际运行通过证据由所属步骤补齐。不接入实际发送或生产写入。

**验收：**每个外部入口能指向具体执行调用链；至少有正常执行、拒绝动作、等待审批、后端失联、重复提交这些可重复场景。跨包实现依赖、共享表写入和共享事务已列入归属清单。每条受保护副作用路径都能指出强制执行点及潜在旁路；后续 step 对比的是同一基线。

独立消费增量另验收：每项平台依赖有退出位置；每个 SC 场景有输入、预期结果、状态 owner、验证方式和责任 step；明确分开本地证据留存、中心同步与外部模型数据流。原已完成项保持历史勾选，新项分别完成后再勾选；本地基线完成不表示整个 step1 中条件性托管详查或团队成本比较已经完成。

**迁移/回退：**基线文档和 fixture 的 change 不改变业务行为；G1/G2 另建修复 change，最小化收紧现有不安全路径并保留兼容说明。不要在安全修复 PR 中混入控制面建模、目录整体迁移，也不能回退到已经确认的越权或盲目重试行为。

### step2 — 更新定位、决策记录与功能归属

**复核修正：**原 step2 由 #195 合入并归档；后续复核见 [step2 复核记录](step2-review-report.md) 与 step2-review-hardening。补齐增量严格准入、研究晋级证据门禁、逐字段漂移及区域完整性、§六逐行处置/独立缺口、契约 owner/发布窗口和候选迁移记录；完成仅表示规划与工具交付，目标执行能力仍须所属 step 的运行证据。

**目标：**正式停止“每个竞品功能都在核心实现”的规划方式。

**操作：**

- [x] 更新 `docs/design/positioning.md`，采用本方案定位。（positioning.md 战略摘要/边界章节已对齐;本次新增交付边界/部署模式/消费者契约章节,失效链接修复）
- [x] 同步第一节的交付边界与部署模式。（positioning.md「Delivery boundaries and deployment modes」三边界+三模式表;ADR-035 批准包名方向）
- [x] 为车商 App 记录消费者契约。（positioning.md 消费者契约段(中性表述:业务 App 私有化交付示例;技术预览/独立生产/受管生产三级门槛;HTTP/JSON 首入口)）
- [x] 在定位与架构中将供应商托管 Agent 服务列为正式执行后端。（positioning.md hosted-backend 段(双轴登记+自托管≠数据驻留);ADR-034 §3;架构文档信任边界节）
- [x] 更新 `docs/design/architecture.md`。（architecture.md 新增「Control Plane, Execution Access, and Trust Boundaries」与能力域/子包/例外登记章节;模块化单体保留）
- [x] 新增 ADR，记录核心职责。（ADR-034(治理语义六决策)+ADR-035(包迁移方向);编号按 INDEX 实时取号）
- [x] 按第六节映射调整 feature-catalog.md。（§六逐行处置映射进入 plan YAML 与受管表；当前实现、目标边界、处置类别、提供方及退出条件分列；保留既有 ID 和交付历史，见复核报告）
- [x] 为上述 P01—P08 映射中的新增平台责任核对 Feature ID。（保留 8.13、9.18—9.20，并以 9.21/9.22 分开登记 Memory 与整体 Knowledge 接入；执行契约、实体责任、本地可靠执行和制品生命周期缺口追加未用后缀）
- [x] 将 `roadmap.md` 的未完成部分改为第七节里程碑。（roadmap 新增「Delivery Iterations」(I-A…I-H)与「Stage Milestones」(M-S…M-R)受管区;Next Phase/Sprint 章节标注 Historical）
- [x] 沿用现有 `feature-inventory.yaml`，先关闭 G5。（G5 已由 #194 关闭;本次 schema 1→2 全量迁移零字段丢失;extract/check 幂等;YAML 头注释覆盖 v2 字段所有权）
- [x] 在字段稳定后增加确定性的受管表格生成。（sync 结构校验、全部 ID/字段漂移诊断、必需区域与 strict=true 完整性门禁；CI 使用 Git 基线，对新增/变更条目严格准入并保留历史欠账，见复核报告）
- [x] 为 research/Labs 条目指定 owner。（8 个 research-candidate 条目(6.15/6.20/6.22/2.12/6.38/6.39/6.42/6.43)生命周期五字段已填(owner=pending 显式待定)）
- [x] 对身份、策略、调度、审批、发布、证据和资产目录等能力，逐项标明。（七类能力约 25 个 ID 已填五元组(responsibility/implementation_mode/enforcement_point/state_owner/milestone),见 catalog 受管处置表）
- [x] 在目录和 roadmap 中按“基础能力域与未来组件边界”标注 Agent Engineering、AgentOps、Agent Control Plane、Agent Governance、安全、评测、MCP/A2A 企业接入层的覆盖和缺口；能力域标签不等于现在的部署单元。（catalog/roadmap 各含「Capability Domains Coverage」受管表:七域现位置/目标子包/职责/缺口;与 architecture.md 能力域归属一致）
- [x] 在架构文档中固定上表的子包 owner。（architecture.md 依赖方向+公开接口规则+分层检查规划+例外登记表(4 项历史例外带迁移步骤/退出条件;owner 按 2026-09-28 决定于 change 启动时指派)）
- [x] 为 Runtime、Memory/Knowledge、Evaluation、Observability 和 Gateway 分别登记契约 owner。（受管 Registry 区分契约 owner、公开契约、独立发行单元、状态 owner 与当前版本/支持窗口；未发布或未验证窗口显式标注）
- [x] 对下方已实现能力逐项给出处置；保留现有 Feature ID、已交付事实和迁移记录，不把移出核心改写成从未实现。（复核报告登记十类候选的代码/数据责任域、安装/挂载证据、使用量待验证状态、替代接口、兼容迁移及回退退出条件；没有候选被判为 retire-candidate）
- [x] 在 `feature-catalog.md` 中为托管执行准入、供应商会话对账、托管工具强制入口和数据驻留门禁设独立验收条目。（2.10c/2.10d/2.10e 新条目(准入+驻留门禁/会话对账/强制入口回执);roadmap M-A 行明示 hosted verification 属里程碑本身,不写为单一供应商专属）

**清单字段迁移：**保留现有 `id/title/phase/category/status/maturity/dependencies/evidence/acceptance` 名称，不新建同义的 feature_id/depends_on/acceptance_evidence。增量增加 `responsibility`、`implementation_mode`、`provider_or_adapter`、`enforcement_point`、`state_owner`、`milestone`、`superseded_by`，并升级 schema/校验器；未适用字段允许显式不适用。`responsibility` 使用 `platform-guarantee / shared-contract / provider-guarantee / optional-ecosystem`；`implementation_mode` 表示 builtin/in-process-plugin/out-of-process/external-service/asset 等实现方式，延期和退役归 status，不混入实现方式。maturity 独立表示验证程度，不由 delivered 自动推导 production。

**验收：**§六本轮映射的每个未实现条目都有归属和明确处置；未纳入本轮的历史条目继续作为显式治理欠账，由所属实施 step 补齐而非自动填充结论；每个候选子包有公开接口、数据 owner、依赖规则和历史例外清单；“权限依赖 Ontology”“跨 Runtime 依赖 Pregel DSL”等不合理依赖被移除；公开市场不再排在内部目录之前。

**迁移/回退：**仅修改文档和规划数据，不抹掉已交付功能及历史链接。研究建议与已接受 ADR 分开标注。

### step3 — 定义并测试平台侧执行契约

**目标：**建立“平台调用 Runtime”的接口，与现有 RuntimePort 的方向区分。

**复核结论：**GLM 合入的三次变更保留了正确边界，但原完成证据不能直接作为验收。复核补齐了真实只读 HTTP 工具回调、入站 Schema 校验、幂等作用域、实时游标续读、能力证据约束、归档与 Sandbox 负例及 CI 门禁。详细缺口、修正落点与复现命令见 [Step3 复核报告](step3-review-report.md)。本步完成的是语言中立契约草案与隔离互操作；不等于已切换平台 Runtime、已验证生产身份/可信制品加载或已认证供应商。

**操作：**

- [x] 将 JSON Schema/OpenAPI 及事件 schema 作为进程外契约的发布源；`contracts/` 中的 Python 类型是对该契约的内置映射，不让第三方依赖 Python 对象、ORM 或平台进程。（`src/hecate/contracts/schemas/` 执行、工具、身份、制品与 Sandbox 权威 schema 集 + 纯 dataclass 映射 + 标准样本三方互检；OpenAPI/HTTP 绑定文档随非 Python 试点 change 交付）
- [x] 为首个进程外接入规定 HTTP/JSON 绑定：能力发现、提交、状态与事件游标、控制命令、错误响应和 artifact 引用；流式订阅是可选视图，断线后仍能用游标恢复。其他传输由 adapter 映射同一语义，不要求所有服务都暴露 HTTP。（`contracts/openapi/execution-backend.http.v0_1.yaml` $ref 权威 schema 不复制；problem+json 四可返回码映射 501/403/429/409，`unreachable`/`outcome_unknown` 为调用方合成态非后端错误；SSE 标 optional capability；`openapi-spec-validator` 结构校验 + HTTP 样本互检）
- [x] 定义 `AgentExecutionBackend` 的最小方法：`describe_capabilities`、`submit`、`get_run`、`read_events`、`list_artifacts`、`request_cancel`。（`src/hecate/execution/backend.py` + Stub 第二实现 + 参数化契约测试 `tests/test_execution/`）
- [x] 将 `provide_input`、`resolve_approval`、`pause`、`resume`、`export_context` 定义为显式可选能力；分别声明 `unsupported / cooperative / enforced` 等可验证控制语义，不能只有含糊布尔值。（三级枚举 + Schema 与映射同时强制非 unsupported 能力的验证条目；未实现的输入/审批不再虚报 cooperative；pause 经实际 HTTP 返回 501，Stub 保留常驻负例）
- [x] 定义 `ExecutionRequest`：任务和运行 ID、部署与版本引用、授权上下文引用、输入及 artifact 引用、预算/截止时间、幂等键、trace 关联、后端专属配置引用。（schema + 映射 + 样本；未知字段容忍且往返保留）
- [x] 将执行请求中的标识定义为带签发域的逻辑引用，不能要求接收方查询平台 ORM。独立宿主从可信本地登记分配 Task/Run/Deployment 标识；受管 adapter 映射平台与后端标识。客户端提供的 ID/授权引用不能替代服务端身份校验或资源查找。（引用类型 kind 分离：平台侧与供应商 session/turn 不可互换，负例测试钉住）
- [x] 定义最小执行制品 manifest：schema/后端类型与兼容版本、定义入口、内容摘要、所需能力、工具 schema 与权限声明、模型/可选组件配置引用、产物 schema 及批准/评测证据引用。后端专属图或脚本放命名空间内，其他 Runtime 无须实现 Pregel DSL；不包含明文凭据、业务数据或在线平台 ID 查找前提。（tar.gz + 逐文件 sha256；拒绝跨平台路径穿越、重复成员、链接/特殊成员、未列入口、未知字段与畸形结构；publisher/license/signature 引用声明可验证条件，真实信任验证由 step5 加载器实施）
- [x] 定义本地安装与加载契约：静态文件/标准归档或 OCI 制品按摘要固定，可信发布者和许可策略可验证；首版不自创专用包格式，也不自动执行清单中的安装脚本。草案格式先由 step5 的本地加载器消费，step11 沿用其发布语义，避免先建完整目录服务才可启动。（首版标准归档（tar.gz），OCI 留待 step11 按需；无安装脚本字段且运行时/Schema 均拒绝未知声明；本地发布者信任根、签名与许可策略、未验证制品处置规则见 contracts/README.md，后续加载器不得以摘要成功代替身份验证）
- [x] 为托管后端定义平台 `Task/Run` 到供应商 session/turn、事件游标、subagent 事件和 artifact 的映射；供应商持有其会话状态，平台只保存必要引用、接收状态与证据。提交响应丢失时先按供应商 ID/幂等能力对账，不盲目重建会话；无法对账时标记未知。（`contracts/hosted-mapping.md` 映射语义 + errors schema `query_by_vendor_session` 策略 + capabilities `reconciliation_support` 声明；subagent 事件命名空间化、绝不自动成为 Team 成员；真实验证属 step8）
- [x] 定义错误语义：不支持能力、授权拒绝、预算不足、版本冲突、后端不可达、结果未知。超时不自动等同于任务失败。（六类封闭集 + 对账指引；version_conflict 覆盖契约版本窗口与幂等键内容复用两种情形）
- [x] 为工具契约固定输入/输出 schema、版本与副作用类别；将参数验证失败、业务拒绝、系统故障、远端结果未知分别表达，并允许工具选择/参数生成与实际执行结果关联到同一 Run。第三方业务工具仍由其所有者实现和维护。（`tool.schema.json` 副作用五值原样采纳内部 `SideEffectClass` 并由测试钉死一致；四态分层——参数拒绝在分发前 400、业务拒绝=工具结果事件 Run 继续、系统故障=工具级错误事件、结果未知=`outcome_unknown`+对账；`tool_selection` 可观察事实关联同一 Run，不暴露隐藏推理）
- [x] 固定跨语言编码规则：ID/时间/枚举/可空字段的表示、未知字段的兼容处理、版本协商、幂等键和 trace 关联；提供请求、事件、错误和回执的标准样本，避免不同语言各自解释状态。（`src/hecate/contracts/README.md` + 标准样本集；请求、事件、错误与 HTTP 回执的扩展字段保留；同一幂等键的请求体/header 一致，作用域来自传输身份；事件尾部/空页继续返回游标）
- [x] 将执行契约与 Memory、Evaluation 等契约分别版本化；定义字段扩展与破坏性语义变更规则、并存窗口、弃用通知和支持终止条件。不得以一个 `hecate-contracts` 包版本要求所有后端同步升级。（执行契约与 Sandbox 契约独立 `$id` 版本空间已建立；0.x 草案不冻结，冻结以 step8 真实验证为前置；Memory/Evaluation 契约在各自 step 建立时沿用同一机制）
- [x] 定义进程外服务身份与授权上下文传递：调用方认证、目标受众、租户作用域、短期凭据引用和回调认证；adapter 不接受客户端自报的管理员身份。（`security-claims.schema.json` 声明集（iss/aud/sub/tenant/delegation_ref/exp），样本带声明集合不带真实 JWT；bearer+mutualTLS 双 profile；回调独立受众凭据禁复用入站令牌；自报 role 落 `extra` 不进鉴权结构——映射行为负例钉住；验证语义归 step7）
- [x] 能力声明附验证来源、适用部署形态、测试时间/版本及失效条件；`pause`、`cancel`、工具代理、沙箱、事件完整性分别认证，不以一项“支持治理”布尔值覆盖全部能力。远程自报事件与平台直接观察的动作使用不同来源等级。（verification 结构含来源等级、测试时间、契约/后端版本、部署形态、证据引用与失效时间；enforced 声明要求独立观察且具备作用域证据；实际来源认证、部署匹配与过期降级由 step4/7/8 实施，声明本身不能授予权限）
- [x] 能力模型把 harness 所有方、环境所有方和工具执行点分开；针对供应商管理的会话逐项声明输入补充、取消、事件续读、回调、内部工具可见性和子任务追踪能力。平台不能控制的操作返回 `unsupported` 或 `cooperative`，不因 API 接受请求而标记 `enforced`。（OwnershipAxes 三轴枚举；除五种交互外增加 cancel/events_resume/tool_proxy/sandbox/callback/internal_tools_visibility/subtask_tracking，缺省 unsupported；完整 hosted 样本明确各项未知能力不默认支持；供应商实际认证仍由 step8 实施）
- [x] 定义供应商配置命名空间；核心只验证通用字段，专属配置由 adapter schema 验证，不能从中读取平台管理员权限。（backend_config_ns 自由对象，核心零校验、零权限）
- [x] 建立 `StubExecutionBackend` 和契约测试；草案接口标记未稳定，允许在 step8 根据真实适配修订。（Stub 即第二实现，满足 runtime-pluggability 两选一；具名消费者为非 Python 试点与 step5a 包装）
- [x] 同时做一个真实非 Python 后端的窄范围 adapter 验证：仅限能力发现、提交、事件/结果、取消语义和一个无副作用工具回调；验证者不依赖 Hecate Python 包。此时保持隔离测试，不接生产凭据或受保护写入，用实际差异修订契约；不能仅用 Stub 冻结接口。（试点 `pilots/execution-backend-ts/` + A 侧 vitest/Ajv 自证 + B 侧 pytest live 参数与 Stub 共享断言；只读工具真正通过 HTTP 回调独立 Python 接收端，入站/回调使用不同受众的合成凭据；原 F1/F2 403/404 绑定缺口已补齐，不继续延期；CI 强制执行异构验证且构建失败不得跳过；契约保持 0.x 未冻结，详见试点及复核报告）
- [x] 单独定义 SandboxProvider 最小契约：能力发现、创建/查询环境、提交/查询命令、文件传输、终止及续租；Sandbox 与命令分别使用幂等 ID，超时结果为 unknown/待对账。定义创建中、就绪、终止中、已终止、失败与状态未知的映射；暂停/恢复等可选状态按能力协商，不强迫所有后端提供快照。（`src/hecate/execution/sandbox.py` + `sandbox-provider-contract` 独立 capability spec；InMemory 测试替身 + 同键异内容冲突、未知结果对账、终止状态不回退、环境存在/就绪检查及跨平台文件路径负例；真实 Docker/云提供方仍在 step7 适配）

**接口边界：**Graph、Channel、WorkerResult、checkpoint 和模型内部消息格式不进入最低契约；外部 agent 自带工具时，也必须申明其可治理范围。

**落点与交付：**建议新增 `src/hecate/contracts/`、`src/hecate/execution/backend.py`、`tests/test_execution/test_backend_contract.py`；`core/composition/` 仅负责注册和选择实现。

**验收：**进程外契约与 Python 类可独立阅读和实现，不导入 SQLAlchemy、FastAPI、Pregel 或供应商 SDK；至少一个不依赖 Hecate Python 包的客户端能按标准样本解析请求、事件和错误。供应商会话/turn 与平台 Task/Run 不混用；不支持暂停的后端提交暂停请求得到明确错误，不能返回伪造成功。

**迁移/回退：**保留 RuntimePort；本步不更换现有执行服务，不宣称已支持新 Runtime。

### step4 — 分离身份、定义、部署、任务与执行

**目标：**让同一个 Agent 定义可以部署到不同 Runtime，让同一个业务任务可以产生多个有关系的执行尝试。

**操作：**

- [x] 保留 `AgentModel` 与现有版本表，把它们视为定义和版本；避免新建同义的 AgentDefinitionVersion。（Deployment 外键引用 `agent_versions` 既有表（models/agent_deployment.py），未新建同义版本体系）
- [x] 增加 Agent principal 及与现有 Agent 的关联：组织、负责人、生命周期、身份提供方映射。负责人是人类或明确的企业责任主体，不是 persona 字符串。（`principal_registry.py` 校验真实 workspace/组织、活跃负责人及其归属；生命周期转换带 workspace；IdP 成对登记。当前支持人类负责人，其他企业责任主体以可解析身份适配接入，不接受自由文本）
- [x] 区分人类发起者、Agent principal、执行工作负载身份与 on-behalf-of 委派。Run 固定所用身份链和目标受众；工作负载证明其部署实例，不直接继承人类或平台管理员权限。（`IdentityChain` 保留独立身份槽及 `audience`；新建平台 Run 校验 workload 的部署引用和活跃 principal 归属，要求非空受众；委派使用 authorization 引用。这里只登记身份绑定，真实凭据/委派/受众鉴权属 step7）
- [x] 增加 `AgentDeploymentModel`：AgentVersion、backend 类型和版本、进程内/本地进程/远程服务接入方式、传输契约版本、endpoint/配置引用、环境、能力快照、接入等级、健康状态、凭据引用。实现语言仅为登记元数据，不决定权限或能力等级。（`deployment_registry.py` 校验版本归属、完整能力快照与 backend/ownership 一致；缺省能力为 unsupported；登记 API 拒绝自行提升 enforced。默认部署使用行锁及部分唯一索引；真实认证提升由 step7/8 接入）
- [x] 为 Deployment 的托管后端配置记录 harness 与环境提供方、服务地区、数据驻留和保留/删除条件、供应商内部工具范围及企业网关路径；Run 固定实际绑定的供应商 session/turn 引用。缺少可核实条件时不得默认判为私有部署或强制治理。（`hosted_config` 校验来源/核验时间及完整条件；完整供应商声明也仅标记 declared，缺失为 unverified。Run 的后端会话不可覆盖，绑定以签发域/ID 规范化约束；认证与驻留策略决策由 step7/8 实施）
- [x] 增加 `TaskModel` 和 `RunModel`：Task 保存业务目标、发起者、验收和责任；Run 保存一次后端执行、尝试号、固定配置及后端运行 ID。（Task 不引入 step6 状态机；新平台 Run 的 `execution_snapshot` 复制版本配置及部署能力/配置/凭据引用，后续部署变更不改历史。秘密值不复制；历史 Run/观察导入未能证明的快照保持缺失，不以当前配置补造）
- [x] 区分平台记录与独立宿主记录：平台 Task 的验收/责任、Run 的后端投影与宿主真实执行状态分别指定字段 owner。新增本地部署来源/本地 ID 映射、事件序号与控制 ownership；独立运行不读平台表，观察接入不能自动取得投递或审批权。（Run 使用 `origin`、`control_owner`、规范化来源键；projection/event_cursor 归平台且先验证再写。历史身份只是来源声明；step6/7 必须在真实投递和审批入口检查 origin/owner，不能把“登记服务没有投递 API”作为权限隔离证明）
- [x] 定义独立部署注册流程：校验宿主身份与信任根、登记已安装版本/能力、显式选择受管的新 Run；历史 Run 仅按来源导入观察，活跃 Run 默认保持原模式。模式转换有操作者、准入与审计，不因网络重连自动改变调度权。（named refs 只登记 pending；准入需要活跃 workspace 管理员、版本/能力和可信解析器的肯定结果；解析器缺失/失败时拒绝。数据库禁止 rejected/pending 同时开启 managed。真实解析器装配、注册/断连/重连和授权租约由 step6/7 交付；本步不声称已有受管执行）
- [x] 定义 `conversation_id / task_id / run_id / backend_session_id` 的映射；一个会话可以产生不同任务，一个任务重试产生新 Run，不能复用旧 run_id 冒充恢复。（`task_run_registry.py` 单写入方：Task 行锁串行分配尝试号；backend 会话以签发域/ID 永久保留，软删除不释放；本地导入按 workspace/部署/来源/本地 ID 幂等，不扫描整表）
- [x] 对旧 Agent 回填 builtin deployment，对已有 session 建立兼容映射；缺少负责人或组织数据的记录标记待治理，不自动赋平台身份。（回填优先有效已发布版本、否则最新有效版本；无版本或未有 principal 者按真实组织与 Agent ID 记录幂等待治理审计。`link_session` 经真实 `Session.conversation_id` 建立映射；conversation 外键指向 conversations，双方必须同 workspace；治理未解析默认 pending，旧执行入口暂不切换）
- [x] 设计增量迁移：先新增可空列/新表，再回填和校验，最后收紧约束。（修复迁移 `e9a4b72c6d10` 新增可空快照/标识字段、校正已存在的 session 映射和组织审计、补约束；重复或不可解析数据明确阻止升级供人工修复，不静默删除。Step4 与修复迁移 downgrade 均明确拒绝；应用回退保留新表及运行/审计记录，不能以删除表的 up→down→up 测试证明安全回退）

**验收：**旧 Agent 在默认 builtin deployment 上仍可执行；同一版本能登记不同后端部署；跨 workspace 不可读取或指定其他部署；重试可追溯到原 Task。

**迁移/回退：**保持旧字段可读，兼容期通过唯一服务层维护映射；不允许业务方独立双写导致两套配置失配。回滚应用时保留新表，禁止自动丢弃运行记录。

**审查证据与后续边界：**见 [Step4 审查报告](step4-review-report.md)。注册值及引用不等价于认证/授权证明；托管条件尚需可信认证，独立宿主解析器尚需真实装配。拒绝审计随登记调用方事务保存，接入 API 必须明确拒绝事务的提交策略，不能无条件 rollback 后声称拒绝已持久审计。step5 才迁移旧执行入口，step6/7 才验证真实调度、权限和断连；Step4 模型完成不能替代这些步骤的验收。

### step5 — 交付独立执行组件，逐条迁移平台入口

**目标：**让业务 App 不安装管理平台即可消费内置执行能力，同时保持平台入口、内置能力和已有客户端兼容。独立发行包是本步必需交付，不延至 step19。

**操作：**

- [x] step5a：先实现 `HecateExecutionBackend` 包装当前 `WorkflowExecutionService`，固定兼容样本；再抽出图编译/Worker/上下文/guardrail 的共享装配函数和执行应用服务。平台 ORM 查询、定义解析、平台授权映射留在平台 adapter，转换为执行输入后调用共享装配；只包装旧服务不能作为独立消费的完成证据。（`runtime-shared-assembly` 已交付：装配落 `src/hecate/runtime/execution_assembly.py`（studio/ORM 零 import，分层守卫扫描全部 import 位点），`HecateExecutionBackend` 在 `src/hecate/execution/builtin.py` 经共享装配执行，契约测试参数化 Stub/live/builtin 三实现，builtin 兼容样本钉住漂移；既有执行服务测试零断言修改全绿）
- [x] step5b：按 step1 依赖闭包抽取 `hecate-runtime` 与最小类型/契约依赖；构建非 editable wheel，并提供声明式 extras/adapter 依赖。清除首个独立 profile 路径上的延迟跨域 import，包括编译输入、工具安全、上下文和证据写入；可选功能未安装时启动声明 `unsupported`，不能等运行中 ImportError。平台兼容导入只转发到新包，禁止新包反向依赖完整 `hecate`。（`packages/hecate-runtime` 落地：内核 88 模块、无 `hecate` 反向依赖、settings/数据库耦合清零（RuntimeConfig+memory 接缝注入）、五行"待解除耦合"清除、wheel 干净安装冒烟 + CI `runtime-wheel-clean-install` job、capability_status 启动期声明；独立基线 §8 证据登记；SC01/SC02 留 step5c 不翻转）
- [x] step5c：实现独立执行宿主（`hecate-runner`），提供本地 manifest 加载、可信配置/secret 引用解析、业务身份和策略 adapter、模型/工具装配、最小执行/状态/事件接口、健康与关闭处理；与平台 adapter 共享执行应用服务，HTTP 身份校验不能以客户端自报角色替代。（PR #208 交付本地预览并补齐身份隔离、参数/能力校验、endpoint 调用、准入审计、串行准入、协作取消与关闭；change `runner-contract-shared-service` 交付 `hecate_runtime.execution_service` 供平台 builtin 与 Runner 共用，Runner 正式支持 `ExecutionRequest`/`SubmitReceipt`/`RunStatus`/`EventPage`/`CancelReceipt`/artifact 引用与 `version_conflict`，wheel 携带与权威 schema 逐字节校验的契约快照，durable profile 的执行事件按稳定 event id 持久化并支持重启后按结构化 backend run ref 查询。验证：Runner 包 100 项测试、平台 builtin/契约 34 项、SC01 干净安装 6 项、scoped mypy 与 wheel 构建通过。固定工具计划和 Stub/endpoint 模型仍是技术预览边界；生产身份/授权/审批/受管组合认证不因本项完成而授予，仍归 step7/8/16。）
- [x] step5c：先提供本地只读技术预览：允许列表工具、参数校验、可信身份、隔离数据域和持久的基础审计必须生效；写工具、后台自动重试、长任务恢复等未验证能力拒绝启用。提供 Stub 模型的确定性 CI 样例和可配置模型 endpoint 的集成入口；真实模型调用证据单独记录，不用 Stub 宣称供应商已认证。（SC01/SC02 随该切片翻转为 `implemented`：`tests/scenarios/test_sc01_cold_start.py`/`test_sc02_inventory_read.py` + `tests/scenarios/tools/`（库存 fixture 与 runner harness）；基线 §5 登记更新，生产认证仍归 step16/step7）
- [ ] step5d：迁移平台调用方，使其选用同一共享装配或独立宿主 adapter；内置执行包与宿主可以单独升级，支持窗口由契约/依赖矩阵界定。Python 嵌入入口复用共享装配但首版不自动授予生产支持；非 Python App 通过公开 HTTP/JSON 样例接入，不必等待 SDK 生成器。（第一切片（change `platform-entry-migration`）：HTTP/MCP/IM/评估四链改经 `EntryExecutionService` 并登记 Task/Run；第二切片（change `entry-tail-migration`）：A2A executor 与定时任务 agent executor 亦经入口服务执行，`llm_service.chat` 直连清除。剩余登记：A2A 协议级 per-agent 身份缺口（调度器 `manager._execute_task` 接线 executor registry 已由 step6 平台轨 change `platform-task-control-api` 完成：cron 触发经 registry 分派 agent/workflow 执行器、结果如实映射 success/failed，空转路径删除）。复核轮（change `step5-review-hardening`，报告 [step5-review-report](step5-review-report.md)）：入口事件存储收敛为单进程共享实例（`core/composition/entry_assembly`），工具装配公开化并纳入分层扫描；A2A 改为显式 `A2A_AGENT_WORKSPACE_ID` 作用域唯一解析（未配置/零/多命中协议内拒绝，全局第一 agent 选取删除），失败响应不再泄露内部错误）
- [ ] 将 Pregel stream、状态、错误和产物映射到平台契约；原始事件作为后端详情保留。（事件映射层已落 `src/hecate/execution/entry_events.py`：RunEventMapper 客户端安全投影 + 后端详情保留 + tool 配对校验；HTTP 流式已切换消费，错误/产物映射待续）
- [ ] 按入口清单迁移：先内部 Agent/Workflow 调用，再评估/定时任务，再 REST/MCP/A2A/IM；每次仅切换一组调用链。（已完成：HTTP chat/agents、MCP agent_chat/session_resume（补齐 event_store/checkpoint 装配与 agent 工具面）、IM 注入适配器、评估 workflow 执行（单一内部 Task 关联）、A2A executor、定时任务 agent executor（后两者为 change `entry-tail-migration`，工具/guardrail 装配与 Task/Run 关联生效，分层扫描纳入 channel/a2a/server）；仍登记：定时任务 WorkflowExecutor 经 studio `WorkflowTestRunner` 测试入口（非 `llm_service` 绕过，独立后续条目）、A2A per-agent 身份（调度器与 executor registry 的接线已随 step6 平台轨完成））
- [ ] 复用 #178 已有的引擎聊天子图和 `CHAT_TOOL_LOOP_ENGINE_ENABLED`，补 G3 的实际入口测试，再加入 workspace 路由和放量记录；不重复实现子图。兼容期明确旧循环不具备的恢复/回放保证，G2 未关闭时也不对新路径授予可靠副作用恢复保证。（已交付：workspace 覆盖（feature flag tenant allowlist）+ 放量审计记录 + 会话路径亲缘；真实入口测试 `tests/test_channel/test_chat_engine_g3_entry.py`（真实 HTTP 执行服务/图/工具 worker，仅 provider 边界 stub）：流式/非流式多轮、审批拒绝（durable APPROVAL 对）、亲缘；取消语义经 backend 契约 REQUESTED，HTTP 取消端点随 step6（已由 step6 平台轨交付：`POST /api/tasks/{id}/cancel`，回执语义同独立命令记录）；断线/恢复（复核轮）：流式中途断连持久化会话快照、恢复后不重复派发已完成的 tool 轮（从恢复的 tool result 直接作答）、全库 TOOL_CALL/TOOL_RESULT 配对完整；顺带修复流式路径未传 user_id/org_id 导致会话状态从不持久化的缺陷）
- [ ] 同步和流式 API 成为 Task/Run 上的等待或订阅视图；不要保留独立执行生命周期。（入口服务同步/流式为同一委托执行的两个视图；事件按 run 引用+游标的持久化读取已随 step6 平台轨对任务控制面派发路径交付并测试，聊天入口的事件持久化迁移复用同一存储，随后续入口切片收口）
- [x] 兼容现有 OpenAI 风格响应和 SSE 格式，在适配层转换平台事件，不把 backend 专属字段强塞给旧客户端。（SSE 从映射后 envelope 渲染，payload 仅客户端安全字段；未关联执行回退原始流并告警，不虚报已登记。已登记执行保持 OpenAI-clean，correlation 信息仅在登记缺失时显式返回（chat 适配层）；`tests/test_channel/test_chat_engine_g3_entry.py` 在真实 HTTP 入口断言 SSE chunk `choices[].delta` 与非流式 `choices[].message` 结构，既有 chat API 回归与新入口 parity 测试全绿）
- [x] 在分层测试中限制入口层新增 `PregelRuntime`、`GraphCompiler` 具体导入。（`tests/test_layering_entry_imports.py`：channel/api、channel/im、tools/mcp 扫描 + 注入负例）

**验证落点：**复用 `tests/test_services/test_workflow/test_execution_service*.py`，增加各入口对同一契约的测试。

**本轮复核修正（基线 `5619cce`）：**真实 Principal ID 解析、Task/Run 关联 savepoint 与安全错误、实际 session 一致性、无工具入口的共享存储、按 workspace 限定的工具定义/执行已补齐；评估改用真实 runtime factory，并传递 workspace、固定 workflow 版本、内部 Task 与 guardrail 装配。builtin 幂等比较纳入图/模型等执行配置，冻结 JSON 输入，不将 interrupt 误报为成功。独立宿主包级测试纳入 CI，干净安装子进程用隔离模式禁止继承源码路径。详细问题、证据与剩余门槛见 [Step5 执行链复核与修正](step5-execution-review-report.md)；5a/5b 的交付事实与 SC01/SC02 的场景状态保留，5c 架构总项恢复未完成。

独立消费另增加包级构建/安装测试和 SC 场景：在不挂载仓库、没有源码 `PYTHONPATH`、不安装完整 Hecate 的环境中安装 wheel/宿主；管理平台地址为空或不可达，运行模拟库存读取和无权请求，核对产物及本地证据。命令、配置、退出码、安装依赖清单和支持能力随包交付；镜像是可选分发方式，不能成为只会从源码启动的掩盖。

**验收：**现有功能回归通过；同一 Agent 从不同入口执行，能关联统一 Task/Run；审批拒绝和终止原因不会因入口不同而变化。

本步分别验收：5a 共享装配行为一致；5b 干净安装与依赖闭包成立；5c 无控制面冷启动并完成只读 SC 场景，未启用写权限且本地证据可查；5d 已迁移平台入口通过兼容测试。部分入口未迁移时只记录该切片完成，不勾选整个 step5。技术预览不承诺持久任务恢复、生产写入、即时远程撤权或高可用，后续由 step6/7、最小 step10/11 与 step16 关闭门槛。

**迁移/回退：**用 workspace 级路由开关逐步放量；仅新任务改变后端路径，活跃任务固定原路径；影子验证只比配置和事件映射，不双执行外部写操作。

独立包按已测试版本锁定；旧平台仍可用兼容 adapter，升级只影响新 Run。发行包退回旧版本前检查制品/状态格式兼容，禁止静默回退到省略身份或审计的裸执行入口。

### step6 — 建立持久化任务、控制命令与治理事件

**目标：**任务不依赖 HTTP 请求存活；执行事实由宿主保存，平台获得可恢复的状态和证据投影；重试不得盲目重做业务写入。

**复核结论：部分完成，不能整体验收。** 已交付持久存储、worker、平台控制 API 和 Runner 本地恢复基础。复核修正了真实平台执行未受租约 fencing 保护、动作台账未注入工具执行链、outbox 提交乱序漏投、恢复身份与受管投影等问题。原先全部勾选的记录混用了契约测试、组件测试和完整宿主验收，以下重新区分。详细问题、验证方法和剩余边界见 [Step6 执行复核报告](step6-execution-review-report.md)。

**已完成的切片：**

- [x] 平台 Task/Run 提交、查询、事件分页/SSE、控制命令与待对账查询 API。提交前固化身份链、Task/Run ID 与输入；提交后平台登记中断可按原 ID 补齐。仅已接通的内置进程内部署允许进入本入口，不能把外部部署登记成功当作可执行。
- [x] 独立 `hecate-durable` SQL adapter、PostgreSQL 参考存储、开发 SQLite、事务治理事件、租约 worker、drain 和有界失败／崩溃重试。第二个真实调度器出现前保留具名接缝，不新增通用调度框架。
- [x] SQL worker 的状态迁移和 Action 意图／领取在事务内校验有效 ownership；同名持有者重启、租约释放和过期均不能复用旧 fencing token。真实平台 dispatcher 传递租约，迟到成功／失败不能覆盖接管方。
- [x] 八种 Task 生命周期与独立命令回执状态；过期命令和过时 `expected_revision` 在执行前拒绝。SQL 唤醒／排队取消的状态、输入或一次性 token 消费、`applied` 回执与事件原子提交；运行中的取消尚无实际效果时保持 `requested`。
- [x] 幂等提交校验调用主体、workspace 和请求摘要，异体／跨域复用拒绝；当前底层原始 key 仍为全局唯一，跨作用域同名 key 返回冲突，尚不是独立命名空间。重放保留原关联，不能生成第二个后端 Run。
- [x] 版本化治理 envelope、重复去重、乱序及缺口标记；平台终态和 outbox 原子提交。中继以逐事件回执而非最大 ID 判定已投递，支持低 ID 晚提交、重启、有界重试和显式毒丸记录；投影失败不能吞掉非去重冲突。
- [x] 真实平台共享装配链和独立 Runner 使用持久动作账本钩子；工具名、参数摘要和副作用分类冲突拒绝，领取和结果通过 execution/tool-call 关联。相同动作的已完成结果可真实回填；未确定结果保留待对账。**这不等于平台完整执行上下文已能跨进程恢复。**
- [x] 本地 Runner 恢复重新验证持久化身份及当前可信配置，不接受重启请求补入新主体／扩大数据域；只调度可执行状态，等待／待对账状态不自动重跑。证据不可写拒绝新受保护动作；回执落盘失败保持待对账；JSONL 清理只删除整日均已过期的文件。
- [x] 受管接收／投影片段：持久接受记录按投递 ID 和请求摘要去重，确认丢失允许重投，已确认项不挤占新批次；上传游标按来源和 Run 分开。平台只接受对应已登记本地 Task/Run 的事件，同 ID 异体拒绝，旧序号不能回滚终态。接收只表示 `queued`，不伪报已执行。

**剩余实施顺序：**以下属于 Step6 的关闭门槛，不转移给 step16；涉及共享服务、身份和审批的前置能力分别与 step5c、step7 同步交付。

- [ ] step6a：完成 step5c 共享执行服务和正式后端绑定，再在 Runner CLI 装配 `ManagedChannel`、持久接收队列、串行执行槽、结果上传及关闭处理。持久接受后宕机，重启只恢复同一 Task/Run；未接通前含 `control_plane` 的 CLI 配置明确启动失败。通过安装后的独立 Runner 进程对接真实平台 HTTP 的最小投递—执行—投影测试。（部分交付(`managed-execution-loop`):Runner CLI 已装配 `ManagedChannel`、持久接收队列(幂等接受,接受≠执行)、串行执行调度、`run_terminal` 结果上传与关闭 drain;重启按原 Task/Run 恢复,standalone replay 与受管调度按 issuer 分流,重连重注册+按 Run 游标补传,缺 durable 的 `control_plane` 启动失败;身份打点漂移转待对账。剩余:安装制品的独立 Runner 进程对接真实平台 HTTP 的进程级测试归 step6f,动作时租约强制归 step6b/step7）
- [ ] step6b：把受管 `LeaseGate` 接入实际 Action 意图／领取／工具派发入口，而不是单独调用验证器。恢复、重连及每个受保护动作验证当前主体、部署、动作范围和授权期限；配合 step7 实现撤权／审批。授权到期后禁止新动作，已有未知结果只对账，不能重新授权后重做。用实际业务 API 调用计数验证断连到期、跨域与旧授权拒绝。
- [ ] step6c：实现独立宿主持久 `waiting_input`／`waiting_approval` 的进入和一次性命令唤醒；等待记录绑定原 Task/Run、参数摘要、审批或输入契约及期限。step7 提供合法审批判定，Step6 提供可靠等待。进程强制终止后重启仍等待，过期／重复唤醒不会派发工具。现有通用 TaskStore 支持等待状态，Runner 暂无完整等待路径。
- [ ] step6d：完成平台共享执行的 checkpoint／动作恢复关联，恢复原逻辑执行和已记录结果；不要仅创建新 Run 并期待模型再次生成相同 execution_id。当前中断尝试已有受保护 Action 时保守进入 `reconciliation_required`。用真实入口验证落盘成功后崩溃、外部写成功但回执失败、未知结果、工具／参数冲突，完成后才关闭整体 G2。
- [ ] step6e：将已测试的父子任务等待原语接到确定性工作流节点或具名外部 workflow adapter；提供经过来源认证的回调入口，绑定父／子引用、workspace、关联键和期限。父子分别重启、跨 workspace 伪造、重复／迟到回调均运行真实接口验收。现有手写 orchestrator 测试不等于工作流产品入口交付。
- [ ] step6f：从非 editable wheel 启动独立／受管组合，在隔离 PostgreSQL 中执行真实进程 kill/restart、等待、租约接管、迟到回执、重复命令、证据故障及重连。测试须统计业务副作用次数并查原 Task/Run、Action 和命令回执；独立安装包不能用仓库源码单测替代。SC03、SC04、完整 SC05 和 SC06 分别按实际覆盖更新场景清单，部分测试保留但不标整场景完成。

**落点：**`packages/hecate-durable` 承载独立契约／SQL adapter／worker，`packages/hecate-runner` 承载宿主装配；`execution/` 承载平台登记、投影和命令入口，`core/composition` 负责绑定。Runtime kernel 只依赖动作钩子语义，不导入平台存储。平台与宿主各自本地事务，不引入跨数据库事务或双主生命周期。

**验收：**提交后断开 HTTP，任务继续；重启不会因确认丢失重复创建同一后端 Run；旧 owner 和重复／迟到回调不能改写实际结果；动作未知显示待对账；命令成功回执必须有已发生的效果。独立宿主未连接控制面也能通过持久等待和恢复；受管组合另行通过授权、投递和重连测试。未达到的能力继续明确拒绝或标注未认证。

**迁移／回退：**平台部署执行 Alembic 后才启动新中继；新增 `durable_outbox_receipt` 为派生投递记录，升级后旧事件可能重放一次，投影必须幂等。独立宿主在停机／drain 后升级本地存储 schema。关闭新入口先 drain 已接受任务；保留 Runtime EventStore，不将 token/superstep 全部写入平台强一致事务。降级前核对状态格式，不能恢复已消费等待或放开待对账动作。

### step7 — 实现与 Runtime 无关的强制治理

**目标：**内置和外部 Runtime 使用同一任务身份、策略、审批、工具与凭据边界。

**操作：**

- [ ] 复用已加固的认证入口，补 `actor / agent_principal / on_behalf_of / delegation_id`；主体和 workspace 来自验证后的服务端上下文。
- [ ] 独立宿主复用动作授权/回执语义，允许业务 App 的身份和权限 adapter 提供可信上下文及本地策略，不强制安装平台 IAM、组织目录或审批 UI。无平台 tenant 时使用客户配置的不可混淆部署/数据域标识；单租户也不可接受任意请求体改写作用域。App 业务 API 再做最终资源权限及状态机校验，不能让模型直写库存表。
- [ ] 将本地必需的上下文校验、Action/审批绑定和证据写入装配为可独立依赖的应用组件，平台和宿主共用其语义/测试；平台目录查询或策略管理 API 留在 adapter，不让独立运行通过依赖整个 `enterprise/ops` 主应用来获得安全能力，也不复制一套更弱的授权实现。新增边界以宿主这一具名消费者证明必要性。
- [ ] 定义本地与受管 policy profile：独立模式使用客户配置的信任根和本地有效授权；受管模式验证中心授权的签发者、受众、部署、主体、动作范围、策略版本、期限及防重放信息。断连不能切换为本地自授权。现有可验证的业务令牌/授权机制优先复用，不为每种 provider 新建身份体系。（部分交付(`managed-runner-enrollment`):受管租约为 `security-claims` 语义的限域限时 HMAC 实现(iss/aud/部署绑定/exp/nonce 防重放),宿主 LeaseGate 验证且无本地自授权路径,断连后租约到期即停;策略引擎对接与完整 profile 归 step7 剩余）
- [ ] 实现平台授权请求：主体、动作、资源、任务、数据类别、环境、委派链及策略版本；先适配现有策略流水线，稳定后允许替换判定引擎。判定服务可外置，但每个副作用必须由 Hecate 工具网关或已验证的执行网关强制执行，并产生决策与执行回执。
- [ ] 子委派权限取父授权、团队/组织策略、目标资源策略和本次任务范围的交集；默认拒绝，拒绝优先，不能通过换 Runtime 扩权。
- [ ] 将工具调用包装为受控 Action：固定 invocation_id、参数摘要、目标资源、副作用类别、授权结果、审批引用、执行回执。
- [ ] 把工具参数及输出校验放在受控动作边界：schema 不匹配不能发送到业务目标；业务失败、技术故障、状态未知与策略拒绝分别记录，重试/熔断不覆盖不确定副作用规则。记录模型所选工具和候选/实际调用的可观察事实，供 step10 诊断；不要求暴露隐藏推理。
- [ ] 仅对明确声明并授权的业务补偿操作记录 `compensates_action_id`、适用条件、审批、幂等与回执。补偿是新的受控动作，不保证撤销已传输信息或外部副作用；不根据工具名自动生成逆操作。
- [ ] REST/MCP/A2A 和内置 Worker 调用同一个 Action 应用服务；禁止入口自行构造无上下文 executor。工具级策略与资源 ACL 的拒绝覆盖模型判断；共享资源明确只读/可写边界，生产禁用匿名模式。已有受保护路径先关闭 G1，再接外部执行后端。
- [ ] 审批绑定参数和资源，支持期限与一次性消费；审批后参数变化、权限撤销或策略硬拒绝时重新判定。并发审批只允许一个合法状态转换。
- [ ] 凭据代理优先代理调用；确需直接凭据时使用短期、限定作用域的令牌。适配器配置只存 secret 引用，事件和 trace 不存明文。
- [ ] 对平台托管后端配置沙箱与网络出口，确保不能绕过受控入口直接使用宿主凭据或访问受保护系统。
- [ ] 对供应商托管后端，只给出经过数据分类准入的输入和有限的工具/数据访问能力；远程 MCP、应用 function handler、供应商内部工具及自托管环境中的本地工具分别评估旁路。企业受保护的读写动作经 Hecate 或认证网关代理执行，网关验证调用主体、委派、参数、审批与撤权并写入回执；无法封闭的直连路径须禁用或降低该部署的治理等级。
- [ ] 包装现有 Docker 环境为 Sandbox adapter，将提供方凭据保留在可信管理侧；绑定 tenant、Run、工作负载身份、隔离 profile、CPU/内存/存储/运行期限和出口策略。凭据优先由代理按目标注入，直发凭据须短期且限定作用域；浏览器登录态同样纳入凭据治理。对创建、exec、文件访问、续租、终止及恢复分别授权。
- [ ] 依据 step1 拓扑做旁路验证：受保护资源拒绝 Runtime 直连；工具调用仅接受受控网关签发的短期授权或受验证的代理调用；Memory、对象存储及 MCP 后端也按资源 ACL 限制，不能因为工具网关已受控而开放其他数据面。无法封闭旁路的外部部署降为较低接入等级，明确标注不提供工具级强制治理。
- [ ] 对 MCP 入站工具调用和 A2A 入站任务分别验证协议凭据、目标受众、租户/主体映射、资源授权、限流和回调来源；出站连接固定允许的目标、数据类别与授权范围。远程 Agent Card、工具描述、检索结果和消息体均按不可信内容处理，不能从内容中提升权限或覆盖平台指令。
- [ ] 为可计量调用设置预算预留和结算，按 invocation_id 幂等记账；远程未知用量标记 estimated/unverified，不宣称可强制控制其内部消费。
- [ ] 明确策略/凭据服务不可用时的规则：未授权的新副作用停止；受管低风险离线行为必须有预先签发且尚有效的限域授权。中心撤销在断连时无法即时传播，profile 必须规定授权期限和最大陈旧窗口；高风险操作要求在线判定，或已被企业批准且当前可验证的本地审批机制。策略版本固定不允许越过本地已知拒绝，时钟回拨/有效期无法可信判断时拒绝依赖该期限的新动作。
- [ ] 重连先验证宿主与当前授权、撤销和期望配置，再允许新受保护动作；历史证据按游标去重补传。过期审批、旧授权和迟到命令不能重新激活动作；未对账 Run 维持原身份/版本与待对账状态。信任根更新与退出受管模式须显式管理员流程及审计，不接受普通控制事件修改信任根。（`managed-runner-enrollment`:每个通道请求重验凭据→准入→信任根解析(撤销即 403 拒绝重连);事件按 event_id 去重补传;重复命令经 command_id 幂等;信任根更新/撤销经操作员流程;期望配置指纹刷新有 API,宿主侧指纹比对、实际执行入口的授权闸门与命令恢复仍未接通，不能以通道组件测试标记本项完成；见 step6a/6b）
- [ ] 关键动作留存 attempt/outcome。外部动作成功但回执持久化失败时进入对账；不声称数据库事务能让外部系统达到 exactly-once。

**落点：**`enterprise/`、`tools/gateway/`、`tools/policy/`、`models/approval.py`、`ops/`，由 composition 接入内置及外部 adapter。

**验收：**更换后端不能绕过审批；旧批准不能用于新参数；委派权限不能扩大；撤销 Agent 后新工具调用被拒绝；重复执行请求不会重复记账或盲目重做外部动作。受保护工具、Memory 与产物对 Runtime 直连请求拒绝，且拒绝有证据；托管后端的远程 MCP/内部工具不能绕过企业网关完成受保护写入，否则该路径被禁用或标注为非强制治理。伪造或跨租户的 MCP/A2A 请求不能创建 Task、调用工具或读取产物；不可信协议内容不能改变平台授权。

**迁移/回退：**先以审计模式比较旧策略和新策略判定，再开启强制执行；双判定不双执行。回退不能恢复已撤销凭据或复活已消费的审批。

### step8 — 接入外部 Runtime，验证并冻结契约

**独立消费关联：**本步验证管理平台能接入不同实现，不要求外部 Runtime 安装 `hecate-runner` 或内置 Python 包。也不假设某个托管 Agent 服务可在离线客户环境运行；执行组件独立运行的认证与供应商服务可达性分开登记。step3/5 的公开契约和调用样例必须在本步之前可用，生态工具完善留到 step18。

**目标：**用真实异构实现验证可替换性，而不是仅让 Stub 通过测试。

**操作：**

- [ ] 按选型表选择一个与 Pregel 内部结构明显不同的 Runtime：许可、自托管、进程控制、事件、工具注入、取消、数据处理、升级和维护情况。具体项目在此步决定。
- [ ] 适配器以独立包/进程部署，通过公开 SDK 或进程外协议接入。至少选一个非 Python 实现来验证 step3 的消息契约；选择依据是与 Pregel 的结构差异和治理能力，不能把语言或 PI 等示例项目写死为产品要求。禁止要求第三方修改核心代码或转换成 Hecate Graph DSL。
- [ ] 先运行隔离测试场景，再接入 step7 的受控工具与审批；能否外置工具执行是强治理接入的重要证据。
- [ ] 对目标 Runtime 逐项执行准入测试：进程/沙箱隔离、凭据可见性、直连受保护资源、出口约束、工具网关强制路径、事件真实性、控制命令实际生效。无法在既有部署中证明的能力标为 `unverified`，不得从供应商文档或 SDK 方法推断为 `enforced`。
- [ ] 补齐启动失败、事件中断、审批等待、取消不支持、进程崩溃、运行结果未知、凭据撤销的测试。
- [ ] 另用一个通用远程测试服务验证黑盒接入行为，不把其能力误写为平台可强制控制。
- [ ] 增加一个真实供应商托管 harness 的独立试点；选型可从 OpenAI Agents API 等服务开始，但适配器和平台契约不绑定品牌。分别覆盖供应商环境、企业自托管环境或无环境中实际支持的组合，不要求单一供应商具备全部组合。验证会话创建/继续、事件断线续读、回调去重、取消请求与实际停机差异、子任务可见性、数据删除及成本对账。
- [ ] 用 step1 的同一任务让内置后端和托管后端分别读取经授权材料、提出测试工单写入、等待人工审批并通过受控网关执行；比较任务完成、审批生效、越权阻断、事件缺口、对账时间、延迟及每任务成本。供应商内建子 Agent 仅映射为 Run 内执行细节，不自动获得 Hecate Team Membership 或独立委派权限。
- [ ] 做企业内部 MCP/A2A 接入试点：把发现/准入、协议身份、租户映射、Task/Run 或 Action 关联、取消回执与审计串成一条链；不以 step17 的跨组织联邦作为内部接入的前置条件。协议版本及可选扩展在 adapter 中协商，不写死为平台内部模型。
- [ ] 按真实差异修订 step3 契约，保留必要的 vendor extension 字段；冻结首个可发布版本并说明向后兼容策略。
- [ ] 编写接入指南，要求实现者只依赖契约，不导入 Hecate 的 ORM、composition 或 Pregel 模块。
- [ ] 接入一个实际云端或自托管 Sandbox 服务，复用同一 Runtime 执行测试；验证创建超时后按请求 ID 对账、命令结果未知不盲目重试、凭据撤销、出口阻断和终止回执。记录具体提供方/隔离 profile 的保证，不把 SaaS 或 microVM 标签当成认证；生命周期失联由平台阻止新授权，并按提供方租约与回收机制对账。

**接入等级：**`hosted_enforced` 为平台托管且已验证身份、沙箱/出口、受保护数据面及动作网关不可旁路；`managed_external` 为外部部署或供应商托管 harness，逐项验证约定控制，必须列出未验证或无法强制的动作；`federated_remote` 仅承诺远程协议、输入输出和可验证控制。等级不等同于 harness 或 Sandbox 的物理位置；托管后端可对经企业网关执行的某些 Action 获得强制控制证明，但不能据此推断其内部工具、会话或网络均受 Hecate 控制。等级由平台准入测试按“部署＋版本＋能力”授予，供应商自声明不能直接生效；能力或拓扑变更后重新认证。

**验收：**同一 Task API、工具权限、审批工作台和证据查询能服务内置、自托管及托管执行后端；非 Python 适配器不导入 Hecate Python 包仍能通过契约测试；不支持的能力被拒绝；关闭外部 adapter 不影响内置部署。托管试点能给出已知数据流、未知/不可验证边界及驻留判定、经网关批准的测试写入及可追溯回执；无法阻断的供应商直连能力明确降级。企业内部 MCP/A2A 入口可映射到同一任务/动作与证据，协议取消失败显示为请求失败或待对账，不伪报执行已停止。

**迁移/回退：**以新 Deployment 承载后端，不修改原部署就地换引擎；路由只切新 Task。活跃 Run 继续使用原版本，失效后通过新 Run 显式重新执行。

### step9 — 分别完成 Memory 与 Knowledge 的替换和治理闭环

**独立消费关联：**Memory/Knowledge binding 同时支持宿主本地配置与控制面发布引用，解析后使用同一能力契约；不得为查询强制访问平台 ProviderBinding 表。无跨任务记忆或文档检索需求的独立 profile 可不安装相关实现；启用后仍须满足本步的权限、来源、生命周期与替换验收。业务库存查询 adapter 不伪装成必须依赖向量库的 RAG。其他语言/远程服务只依赖其已使用的契约，向量库及模型均由使用方选型。

**目标：**企业能够替换记忆与知识服务；选择组件化 Knowledge 实现时，也能分别替换索引/存储和检索流水线组件。两者复用登记、绑定、授权和证据规则，但分别拥有契约、数据和发布版本。不要求所有外部 RAG 服务开放内部阶段。

**交付切片：**9a 先落共同绑定及能力声明，9b 完成 Memory 替换；9c 完成 Knowledge 接入和生命周期，9d 完成检索组合与替换符合性。9c/9d 可以先在合成数据和可选组件中验证，不以真实客户知识库为前置。可在不同 change 中并行开发 9b 与 9c，但每个切片独立给出迁移、权限负例及回退证据。

**操作：**

**9a：共同绑定治理。**

- [ ] 增加按能力区分的 ProviderBinding：平台模式使用组织允许列表、workspace 默认与 Deployment 显式绑定；独立模式由可信本地配置提供允许列表与绑定，不加载组织目录。Agent 只能在授权范围选择。Run 固定 Memory 与 Knowledge 各自的绑定、能力及版本快照；知识/索引数据归对应提供方，平台或宿主只保存必要引用和证据。
- [ ] 为整体 Knowledge 服务和组件化实现分别定义能力声明。整体服务可只支持授权检索/引用/删除等端到端契约；组件化实现另声明 parser、chunker、embedding、dense/sparse/full-text、hybrid、reranker、query processor、graph retrieval、阶段诊断。未声明能力显式拒绝，不用统一接口伪造内部 trace。
- [ ] 公共契约保持语言及供应商中立：Document/Chunk/SourceRef、KnowledgeSnapshot、SearchHit/Citation、IndexJob/QueryRun 引用与标准错误；内部 adapter 仍可使用现有 Python 类。检索结果要关联来源版本、证据定位、权限/数据分类及所用配置版本，不向公共 DTO 泄漏 Qdrant ID 类型、固定向量维度或模型名。

**9b：Memory 替换。**

- [ ] 将 MemoryProvider 类型提取到中立契约层，通过兼容 re-export 保持已有 provider 可用；不立即删除旧能力名称。
- [ ] 将内置评分公式、ReflectionEngine、固定 prefetch 策略列为可选扩展，不作为第三方最低契约。
- [ ] 最低读写契约携带服务端注入的 namespace、来源、数据类别、版本和访问范围；provider 返回值在离开边界前校验归属。
- [ ] 区分 `no_hits / unavailable / unauthorized / unsupported`。个性化记忆可以按策略降级；必须依赖授权事实的任务不能把后端失效伪装成空结果继续执行。
- [ ] 为不同 Runtime 明确谁负责记忆注入和写回，每个 deployment 只选一个责任方，防止平台和 Runtime 重复注入/重复整合。
- [ ] 接入一个实际第三方后端，覆盖检索、增改删、能力缺失、数据驻留和跨租户负例；具体项目按契约选，不固定 Mem0。
- [ ] 用公开进程外契约验证一个可独立升级的 Memory adapter；跨语言实现按实际候选选择，不要求每一种 Memory 后端都提供 Python 插件。验证只升级该 adapter 或服务、内置 Runtime 和控制面保持原版本时，既有绑定继续工作且新绑定只接受已通过兼容测试的组合。
- [ ] 定义导出清单、来源 ID 映射、加密传输和删除回执；删除范围包含索引、派生数据及缓存，备份处理按保留策略记录。
- [ ] 迁移默认采用快照导出、导入验证、限定写入切换窗口、再切新绑定。必须在线迁移时才增加变更捕获与一致性水位，不先引入双写框架。

**9c：Knowledge 接入和生命周期。**

- [ ] KnowledgeProvider 复用共同绑定治理，但保留独立检索/文档契约。来源连接器/解析器可替换；支持文件与企业系统提供的授权来源引用，不承诺自建通用 ETL。记录源对象 ID、摘要/版本、变更游标、索引水位、数据分类和 ACL 版本；只在来源许可及最小权限内同步。
- [ ] 接入作业记录解析、切片、Embedding、索引各阶段的状态、错误、重试和输出引用。新增、修改、去重与删除应可对账；源数据更新先构建并验证新索引，再原子切换可检索版本，失败保留已授权的上一版本或停止该来源查询，不能将半成品标为可用。
- [ ] 文档级/行级及派生 Chunk/索引权限按服务端身份约束；检索阶段在相应后端执行过滤，结果离开边界前再验证归属和 ACL。权限撤销及删除覆盖源、索引、缓存、派生产物；受支持的后端提供完成或未完成回执，不具备必需传播能力时拒绝绑定。
- [ ] 文档和表格解析、切片、去重及索引质量由可替换检查器提交版本化结果；最小门禁识别空内容、丢失来源/表格结构、重复和索引不一致。页码、段落、表格行列等证据位置在有能力时保留；不能定位时 Citation 明确标记粒度。

**9d：检索组合与独立替换。**

- [ ] 支持两种接入：完整外部 RAG 服务承担内部流水线；或可选 Knowledge 实现按公开组件契约组合 Loader/Parser、Chunker、Embedding、VectorStore、全文检索、Retriever、Reranker、Query Processor。平台不要求后端把内部算法移植为 Python 包。组件化时固定每段版本、输入输出 schema 和责任方，整个 pipeline 形成可发布的组合版本。
- [ ] 复用已有 `hecate-memory` 的 VectorStore 与 Qdrant/Chroma/Milvus/Weaviate adapter 作为参考起点；先做真实替换和能力测试，不重新设计一个同义向量接口。向量库选型由企业决定，新后端按接口与符合性测试接入；索引元数据记录 embedding 模型、向量维度、距离度量、稀疏/全文支持及 ACL 过滤能力，不兼容组合拒绝新绑定或执行显式重建。
- [ ] 检索配置允许基于同一授权快照比较 dense、全文、hybrid、重排和查询变换。阶段证据可携带变换后查询、候选集/分数、融合/重排配置、耗时、费用及所用索引版本；敏感数据按 ACL 和保留策略处理。单独只替换 Embedding、向量库或 Reranker 时，其他能力无需同步发版；既有 Run 固定原组合。
- [ ] GraphRAG 作为可选 Knowledge 扩展登记图谱 schema/制品、抽取器/实体消歧器和源证据版本。图检索通过同一授权 SearchHit/Citation 契约参与比较；业务 Ontology、实体规则和图数据库由场景或外部组件提供，不成为基础 Knowledge 契约的必填项。

**关联的持久工作区治理。**

- [ ] 为 Sandbox 持久工作区明确文件数据 owner、ACL、存储引用、配额、保留/删除和导出规则；可复用既有存储服务，不把文件目录伪装成 MemoryProvider。已交付产物先写入受治理产物存储再允许环境回收。默认通过文件/产物导出迁移，不承诺将运行内存或进程快照跨供应商搬迁。

**验收：**Memory 和完整 Knowledge 服务分别通过租户隔离、授权检索、来源追踪、撤权/删除测试。组件化实现至少验证一个非默认向量库和一个不同 Embedding 或检索组件：切换向量库不改控制面/Runtime；Embedding 不兼容时拒绝复用旧索引并指导重建。相同数据集与权限快照可比较检索结果、阶段延迟和费用；不支持内部诊断的完整服务仍可参与端到端评估，但不能获得阶段诊断认证。9c 的失败注入证明同步中断不会发布半成品索引；不能保证删除或驻留的后端无法绑定要求这些能力的任务。

**迁移/回退：**Memory 保留旧后端只读及迁移映射；切换后新写入需要逆向迁移或明确暂停。Knowledge 索引变更采用新版本构建、验证、切换及旧版本受控回收；换 Embedding/向量库不承诺原地兼容。回退新查询路由不能复活已撤销的数据或权限。

### step10 — 分离观测、评估与企业证据

**独立消费关联：**最小本地证据 envelope 在 step5/6 即交付，本步扩展独立查询/导出与集中投影；独立运行不要求部署集中 Trace UI 或评测服务。发布评测可以在开发/CI 环境生成并随制品携带可验证结果，客户环境校验其版本及门禁即可；不因此跳过运行时授权、健康和审计。默认不向中心上传 prompt、业务数据、产物或 trace；export adapter 配置允许字段、脱敏/引用、目标和失败保留策略。外部模型调用按数据分类另行授权，与遥测开关无关。中心离线、导出失败和本地证据存储失败必须分别表达。

**目标：**允许外部系统承担 trace 与评估执行，同时保持 Hecate 的治理和发布责任。

**分段边界：**step1 的测试 fixture、step6/7 的关键证据与版本关联、step11 所需的最小评测结果先行；本步扩展外部 evaluator/exporter、线上抽样和完整证据生命周期。不能等待选定 LangSmith 等服务才启动评测或记录授权证据。

**操作：**

- [ ] 定义存储边界：Runtime 日志由后端管理，调试 trace 可外送，平台授权/审批/委派/发布证据独立保存。
- [ ] 扩展现有 OTLP 导出，关联组织、Task、Run、AgentVersion、Deployment 和父子委派；导出前按数据类别脱敏或仅发送引用。
- [ ] 将 trace、prompt、工具参数、交接包和产物元数据视为可能含敏感数据的独立资源；按字段分类、租户和任务 ACL 控制查询、导出与保留。远程自报的 trace/完成状态标注来源及验证等级，不能直接作为关键动作的执行证明。
- [ ] 对托管后端分别接入供应商 session/turn 事件、trace 导出和用量；供应商记录可补充诊断与运行成本估算，平台授权决策、网关 Action 与审批回执仍由平台证据链独立保存。缺失、延迟或可修订的供应商用量标为未核实，不当作零成本或最终账单。
- [ ] 接入一个外部观测目标，用标准接口验证。供应商 UI deep link 作为可选扩展，不成为证据唯一入口。
- [ ] 观测 exporter、评估执行器与治理证据存储分别记录版本和故障边界；更换或升级观测/评估提供方不修改 Runtime 事件格式，也不要求迁移平台关键治理证据。
- [ ] 定义 EvaluationBackend 的提交、查询、取消和结果接口；将现有评估 engine 作为 builtin 实现。
- [ ] 将评测分为发布前离线回归和上线后抽样评估：数据集、样本来源、评估规则/人工复核、触发时机和反馈去向分别记录；线上评测只提供质量信号，不能代替关键动作的授权审计。
- [ ] 定义结果 envelope：运行配置摘要、Agent/Team 版本、数据集及评估器版本、样本数、指标定义、失败数量、阈值、证据引用和结果完整性状态。
- [ ] 增加版本化实验引用：数据集/知识快照及权限快照、检索流水线/组件与索引版本、模型/Prompt/工具 schema、运行环境和评估器版本。一次实验由逐样本 trial 与可复算汇总组成；对随机输出固定运行次数与统计方法。外部 evaluator 可提交同一 envelope，不要求把全部原始资料复制进 Hecate。
- [ ] 按 P01/P03/P08 分离检索、回答和动作结果：允许上报 Recall@K/MRR/nDCG、答案正确性/faithfulness、引用正确性、工具选择、实际任务完成、延迟/token/成本，指标定义和缺样本状态明确。分组切片至少支持问题类型、来源文档、权限范围、后端/组件版本；数值由可替换 grader 计算，平台不强制内置所有算法。
- [ ] 在组件化 Knowledge 路径上关联查询改写、召回、ACL 过滤、融合、重排、引用构造的阶段事件和配置摘要，以定位失败及比较代价。完整外部 RAG 只可提供其实际可验证的阶段；没有阶段数据时标为 `unsupported/unverified`，仍可进行端到端评测。调试 trace 依旧可采样，发布与权限证据不依赖采样。
- [ ] 将失败样本经授权、脱敏和人工复核后纳入版本化回归集；上线候选仅与相同数据/权限快照和兼容指标定义的基线比较。不同 Embedding/索引、模型、检索组合可分别运行，不能用同名指标掩盖不同评测条件。
- [ ] 不直接比较不同评估器的同名分数；发布规则必须固定可比较的指标与基线。超时、缺样本、版本不符不是通过。
- [ ] 将现有发布 gate 改为消费统一结果，外部评估回调需要认证、去重和目标版本校验。
- [ ] 为关键证据设计独立写入权限和完整性校验；如需要防篡改，使用受限存储/外部锚定等明确机制，不能把单纯 hash-chain 称为防篡改证明。
- [ ] 明确保留期、访问授权、删除/冻结要求和证据导出；具体法规映射后续按企业适用范围配置，不宣称自动获得认证。

**验收：**停用外部 trace 系统或托管供应商 trace 导出延迟时，Task/Run 和关键授权证据仍完整；供应商自报工具成功但缺少网关回执时，受保护 Action 不记为已验证执行。外部评估可阻止或允许发布；切换评估系统不修改 Agent 发布 API。同一权限/知识快照下能比较两种真实检索组合，定位到可观察阶段或明确显示阶段不可见，并区分“回答正确、引用错误”。离线回归和线上抽样结果分别绑定数据集/样本来源与规则版本，样本缺失或人工复核未完成时不能伪报门禁通过。

**迁移/回退：**先增加 exporter 和 adapter，再减少内置重复界面；故障回退到 builtin evaluator 需重新产生结果，不能挪用另一评估器的通过记录。

### step11 — 建立 Agent 登记、准入与发布闭环

**目标：**“能注册”不等于“能执行高风险任务”，部署组合与评估证据绑定。

**最小交付先行：**先支持内置 evaluator、已有 Memory 的固定引用、builtin 或已认证外部部署，完成发布清单、缺证据拒绝、停止新运行及回滚。未使用的能力不设虚假绑定；step9/10 后续提供更多替代实现。本步不以建成所有 provider 为前提，团队试点依赖这个最小发布闭环即可启动。

独立交付沿用 step3/5 的执行制品格式：开发/CI 负责构建与评测，客户部署的校验器负责接受、激活和运行准入，不需要在线发布中心。step5 的摘要/可信来源验证与本步完整签名、评测回执、撤销和升级门禁是递增能力，不各自维护一套格式。生产 profile 必须先固定信任根和允许签发方；签名有效也不能替代客户本地权限或当前拒绝规则。

**操作：**

- [ ] 增加生命周期：draft、pending_review、approved、active、suspended、retired；绑定负责人、用途、数据范围和治理等级。
- [ ] 复用 AgentVersion/WorkflowVersion/SkillVersion，创建发布清单，固定 Runtime adapter、能力快照、Memory/Knowledge binding、工具 schema、凭据引用及评估证据。
- [ ] 发布清单分别固定每个已绑定能力的契约、adapter、服务实现及必要的数据格式版本；准入只验证本次组合，不能把一个组合的测试结果自动继承给同供应商的其他版本或其他部署形态。
- [ ] 涉及知识检索的发布组合另固定完整 Knowledge 服务版本，或组件化 pipeline 的 Loader/Parser、Chunker、Embedding、VectorStore/全文索引、Retriever、Reranker 和可选图检索版本；同时固定索引快照、来源/ACL 水位与引用能力。组件缺失时只记录实际启用项。变更 Embedding/维度、索引或权限传播语义须重新构建/验证相关索引及运行评测，不把普通配置切换当作已完成迁移。
- [ ] 对供应商托管服务，在发布清单中固定可获得的模型/harness 配置、服务地区、会话保留与删除条件、环境提供方、内部工具白名单和可控 Action 范围；无法固定的供应商行为列为持续监测的外部依赖。供应商 API、策略、地区或控制语义变化时重新准入和回归评测，不宣称能完整重建不可导出的内部状态。
- [ ] 为 Agent Engineering 固定从源配置到发布制品的可复现记录：源版本/摘要、构建输入、依赖与 adapter 版本、契约/安全/评测测试结果、制品摘要；允许调用企业 CI/CD，平台只消费签名或可验证的构建/测试回执。
- [ ] 增加本地安装/升级流程：校验 manifest schema、内容摘要、签名/签发者、后端与状态格式兼容、所需能力和权限，再 staging、健康验证、原子切换新任务路由。任一步失败保持旧版本，禁止带明文凭据、路径穿越或未批准安装脚本的制品；运行中任务保留原版本与配置。
- [ ] 离线交付以标准归档或 OCI 镜像及可验证 manifest 提供执行定义和必要 adapter/依赖清单，信任根、签名密钥轮换及撤销材料由受控运维流程交付；模型权重/外部服务不假定可随包分发。完全隔离网络另验证依赖齐备、许可和无外部解析请求，不能把仅无控制面启动标成 air-gapped 认证。
- [ ] 制品版本回退不回退业务数据、审批消费、Action 回执或已知撤销；状态格式不兼容时先 drain/备份/迁移并验证，再激活，不能自动执行破坏性 downgrade。离线撤销材料存在新鲜度上限，过期后的可用能力依 step7 profile 明确收窄。
- [ ] 发布清单和准入门禁是平台治理契约；Agent 目录、镜像/制品仓库、CI/CD、评估器和部署控制器可由 Hecate 默认实现，也可接入企业现有服务，不为满足“闭环”重复建设完整 ALM/市场。
- [ ] 策略版本用于复现，但授权仍叠加当前硬拒绝和撤销状态；不能让旧发布清单保留已取消的访问权。
- [ ] 准入检查实际能力、隔离、许可与供应链、数据出境配置、健康及评估结果；未知能力不按支持处理。
- [ ] 对 Agent、Skill、工具、连接器和 adapter 的发布资产建立权限/数据流 manifest 与风险分级；代码执行、任意 URL、数据库写入、浏览器控制、敏感数据处理等能力触发专项审查。签名只证明来源与完整性，不能替代行为测试和沙箱/出口验证。
- [ ] 发布到 DEV/STAGING/PROD 的独立部署与凭据范围，禁止复制生产明文密钥。
- [ ] 灰度按新 Task 分流，分别观察质量、成本、取消/审批可靠性；不在 Run 中途换运行时或记忆绑定。
- [ ] 退役时停止新任务，处理活跃任务，撤销凭据，保留证据与必要的数据退出记录。
- [ ] 将 Sandbox 模板摘要、提供方/契约版本、隔离 profile、资源与出口策略纳入发布清单；快照单独记录内容覆盖范围、源环境、敏感级别、ACL、兼容格式和保留期。恢复或 fork 重新授权并重新绑定短期凭据；不能以旧快照恢复已撤销权限。缺少运行状态恢复能力时，以新 Run 显式继续，不能伪报原 Run 无损恢复。

**验收：**平台拥有的发布配置与证据能够完整重建，供应商内部不可导出的会话状态及行为差异被显式标注；工具 schema、数据处理条件或后端治理能力变化触发重新验证。回滚指向旧发布组合而非只切 prompt 版本，且只影响新 Task。

**迁移/回退：**现有已发布 Agent 映射为 legacy deployment，标明未验证的治理等级；允许受控兼容使用，不自动授予新认证。

### step12 — 增加企业团队与成员模型

**独立消费关联：**企业 Team/组织目录是管理平台的可选能力，不成为执行组件的安装前提。单宿主内的多 Agent 调度不自动具备企业成员身份、跨组织授权或平台团队认证；业务 App 确需团队能力时再接入本步契约与后续协作服务。

**目标：**团队成为长期平台资产，团队成员不等同于某次图执行中的节点。

**操作：**

- [ ] 新增 `collaboration/` 领域及 Team、Membership、RoleDefinition 模型；与现有 org/workspace 关联，不另建第二套租户树。
- [ ] 若客户已有组织/团队目录，允许目录同步或按需查询；Hecate 仍保留稳定主体引用、任务上下文、授权判定和资源 ACL，不把外部目录中的成员关系直接当作资源访问许可。
- [ ] Team 设置负责人、目标、允许部署、数据共享边界、预算、升级联系人和默认验收规则。
- [ ] 区分角色描述、任务职责、授权 grant；提示词中写“管理员”不能产生权限。
- [ ] Membership 支持加入、暂停、离开和有效期；Agent 可以加入多个 Team，但每次执行选择明确的团队和授权上下文。
- [ ] 跨 workspace 加入通过双方明确授权，成员关系不自动授予资源读取权；每个 Artifact/Memory 的共享仍需资源级授权。
- [ ] 团队新增成员或开放任务协作时，重新计算共享沙箱文件、Memory、Artifact 与 Connector 的可见范围；发起者的私人凭据和连接器授权不自动随任务分享。需要团队共用的连接器须单独绑定团队主体、范围与撤销条件。
- [ ] 先实现固定团队和人工分派；动态创建团队必须经过相同准入，不能由模型直接改成员表。
- [ ] 明确供应商内部生成的 subagent、turn 或 handoff 只是其后端会话内的执行记录；只有经过 Hecate 登记、准入和委派的 Agent 才能成为企业 Team 成员并取得独立主体权限。

**交付：**Team CRUD、成员变更 API、权限矩阵、管理页面和隔离测试；模板只是创建这些配置的入口。

**验收：**同一 Agent 在不同团队拥有不同任务权限；移除成员后无法读取新任务和新增共享资源；已有授权和历史证据按明确规则处理。

**迁移/回退：**已有多 Agent workflow 保持可用，不自动解释为具有企业权限的 Team。可在受控导入中将拓扑转为候选团队配置，由管理员确认。

### step13 — 增加持久化团队任务与委派

**目标：**团队能跨 Runtime、跨进程完成可恢复、可验收的任务。

**操作：**

- [ ] 增加 Assignment、Delegation、TaskDependency、Artifact/ArtifactVersion 和 AcceptanceRecord；复用 step4 Task/Run，不新增同义任务表。
- [ ] 将模型生成的分解计划视为提案，校验依赖无环、成员资格、输入范围、预算和截止时间后才能创建子任务。
- [ ] 候选选择先做权限和能力过滤，再做负载/质量/语义排序；复用现有 TaskAllocator 作为排序策略，不让它决定授权。
- [ ] 委派绑定父任务、授权范围、预算上限、最大深度和期限；子任务不得读取完整父上下文，只接收显式交接包。
- [ ] 交接包明确字段、数据类别、来源版本、接收者、有效期与再委派限制；人类接管或重分派视为新的授权事件，旧接收者对后续产物和连接器的访问按策略撤销。
- [ ] 使用 step6 的持久化作业和事件执行分派、领取、超时、重试和重新分配，不用进程内 EventBus 保存唯一任务状态。
- [ ] 产物保存内容摘要、来源 Run、版本、数据类别和 ACL；下载走授权代理或短期授权，不能用猜测对象地址访问。
- [ ] 已完成子任务的产物只有验收通过才影响父任务。父任务成功需满足规定验收，不以最后一个 Agent 回复“完成”为准。
- [ ] 对不确定的副作用进入对账/人工升级；只对声明支持的动作执行补偿，补偿本身仍需授权和审计。
- [ ] 对确定性流程与 Agent 任务分别记录状态 owner、输入/产物及回调来源；Agent 计划只能提出候选转移，业务状态机验证后才能提交。外部流程引擎可经平台任务/事件/命令契约接入，不必移植为 Pregel 图。
- [ ] 动态协作设置无进展、成本、深度和时间停止条件；默认不启用无限轮讨论。
- [ ] 默认按任务及信任范围隔离环境，Agent 间以授权交接包/产物协作；确需共享持久工作区时显式配置成员、并发写入规则和锁/版本冲突处理。复用环境前验证安全配置并清理用户数据、凭据与浏览器状态，无法证明清理有效时重建环境。

**验收：**两个异构 Runtime 完成“收集→复核→审批→写测试工单”的流程；任一 worker 重启不丢任务；超时重分配不会重复创建工单；撤销父委派能阻止后续子动作。

**迁移/回退：**只为新团队任务启用协作调度；已有 workflow 作为可调用 deployment，不强制重写其 DAG。停用调度前处理 outstanding assignment。

### step14 — 建设人类观察与干预工作台

**独立消费关联：**step6/7 即提供最小状态查询、审批/输入和控制命令接口，独立 App 可以实现自己的界面；I-Ca 验证本地审批，I-Cb 验证平台最小视图，本步再提供完整团队工作台。后端/宿主始终校验当前权限、参数和状态版本；前端按钮不能直接写审批表或把 requested 显示为 applied。未连接控制面的部署，其实时观察与干预只能由本地授权客户端完成，中心不虚报实时可控。

**目标：**人类能够了解团队工作进度并可靠地改变执行，而不是只看到流式聊天。

**分段交付：**I-Ca 由业务 App 或测试客户端消费本地状态/审批接口；I-Cb 复用平台现有审批界面或增加薄页面，展示 Task/Run、审批和控制命令真实状态；只依赖 step6/7 的 API。团队关系、共享权限可视化及复杂接管流程在 step13 后完成，不能把基本批准/拒绝和阻止新动作延至团队工作台全部建成。

**操作：**

- [ ] 在现有 `web/` 增加任务列表、团队工作台、委派关系、产物、预算和待审批视图；复用现有 trace/回放链接，不再建重复分析平台。
- [ ] 将干预实现为持久化命令：补充信息、审批/拒绝、请求暂停、取消、重新分派、接管。显示请求中、已确认、已应用或不支持。
- [ ] 本地工作台和审批流只是默认客户端/实现；审批可委托外部 ITSM/工作流，但必须用带版本和身份的回调更新统一命令状态，且回调认证、幂等与审计由平台契约约束。
- [ ] 命令携带 expected task/run revision，防止人类对过期页面操作；重复点击采用幂等键。
- [ ] 人工审批支持职责分离：任务发起者、提案 Agent 和审批人不能因方便默认合一，按企业规则配置。
- [ ] 后端不支持暂停时，明确提供“禁止新受控动作”和“请求取消”等可用操作；不得显示已经暂停。
- [ ] 对托管会话分别展示供应商确认的运行状态、平台侧新 Action 阻断状态和取消请求状态；供应商接受取消请求、关闭观察流或终止 Sandbox，均不能直接标记为会话已停止。若供应商允许继续输入或会话引导，界面标注其生效边界及回执来源。
- [ ] 接管先确认原执行状态和副作用，再转移批准的输入/产物；原 Run 无法停止时必须显示冲突风险和资源授权处理结果。
- [ ] 在工作台展示每个协作者可见的产物、Memory、沙箱工作区与可用 Connector；增加协作者或跨团队转交前显示授权差异并要求既定策略中的审批，不在界面中暗示共享对话等于共享全部数据。
- [ ] 恢复时重新检查权限、预算和审批有效性；人的新输入从哪个执行边界生效要有回执。
- [ ] 对人工审批/接管展示后端的真实恢复等级：事件回看、隔离重跑、原 Run 恢复分别呈现；只有后端确认恢复才继续原上下文，其他情况创建关联的新 Run。显示补偿请求、执行和确认状态，不把“已请求补偿”显示为外部结果已撤销。
- [ ] 只展示动作、事实、证据和可解释摘要，不要求 Runtime 提供隐藏推理过程。
- [ ] 工作台分别展示 Run 状态、Sandbox 状态、租约/预算和持久工作区；暂停环境、取消命令、取消任务是不同控制操作。人类接管终端/浏览器使用短期授权会话并留下回执；不支持接管或强暂停的后端显示限制，断开观察连接不自动视为任务终止。

**验收：**人类能在后端等待时拒绝写操作；页面断线重连不丢事件；后端拒绝取消时页面保持真实状态；越权用户不能观察其他团队的敏感产物。

**迁移/回退：**工作台使用统一 Task/Run/command API，不连接 Runtime 内部数据库；可独立回滚界面而不丢已提交命令。

### step15 — 实现有边界的自治理

**目标：**形成“检测→决定→处置→验证→恢复”的治理闭环，避免直接让 Agent 自行修订权限。

**操作：**

- [ ] 首批规则只覆盖可验证事件：连续失败、预算阈值、心跳失联、越权尝试、异常工具频率、评估回归。
- [ ] 处置动作限定为告警、限制新任务、降低并发、暂停部署准入、撤销授权、切换到已批准版本、请求人工复核。
- [ ] 每条规则记录作用范围、证据、冷却期、触发次数、责任人和恢复条件，防止告警与自动回滚互相触发形成循环。
- [ ] AI 风险分析只提交结构化建议或在明确授予的低风险动作内执行；硬拒绝不能被模型评分覆盖。
- [ ] 对自学习 Skill/prompt 候选复用已有评估和人工发布门禁，学习器不能修改生产发布引用。
- [ ] 增加自动处置审计和幂等键；将“处置命令已发送”与“系统已恢复”分别验证。

**验收：**测试注入预算超限后，新动作被阻止且证据可查；误报可以人工解除；自动恢复必须符合既定规则，不自动恢复被安全管理员明确撤销的权限。

**迁移/回退：**默认先只观察，再按 workspace 启用自动动作；一键停用自动处置不删除证据和当前有效的硬拒绝。

### step16 — 验证生产部署、恢复与符合性

**目标：**把前述契约变成经故障和多租户验证的生产能力。

**执行方式：**把下面清单按当前交付范围纳入每个迭代的验收。首次内置闭环就验证重启、租约、授权和事件可靠性；新增外部后端时验证其故障与撤销；Memory 迁移、团队协作、独立发布各自上线前验证。无需等自动自治理、跨组织联邦或公开生态建成后，才发布一个范围受限且经验证的生产 profile。

**操作：**

- [ ] 指定 PostgreSQL 为参考生产持久化配置；其他数据库/消息/存储组合按测试结果声明支持等级，不自动继承同等保证。
- [ ] 分开发布独立只读技术预览、独立可靠生产、受管生产和完整私有平台的支持矩阵，记录网络模式与所选 adapter。前两者的冷启动环境不存在管理服务/平台管理表；受管模式另验证连接、断连和重连，不把“曾连接后缓存可运行”代替从未连接的独立冷启动。
- [ ] 用构建出的 wheel/镜像在无源码路径的环境安装；核对依赖树、配置、启动/停止、模型和业务工具调用、本地证据、未安装可选能力及错误提示。清空控制面地址并阻断其网络访问，SC 场景仍通过；RAG/Memory/Sandbox 未启用时不得被隐式安装或请求。
- [ ] 按 SC 组演练：无权库存读写拒绝、审批等待时重启、写成功但回执丢失、授权过期/时钟异常、审计盘满、中心断连/重连、重复命令和旧 ownership。逐项验证是否继续/拒绝/待对账，不只看进程存活；本地与受管各保留独立报告。
- [ ] 捕获安装、启动、运行与同步时的网络请求，验证只访问明确配置的目标；遥测关闭时中心无业务内容，调用外部模型时仍如实列出出站数据。完全隔离网络 profile 在阻断外部网络后另测本地模型/依赖与信任材料有效性，未测则不授予该标签。
- [ ] 演练制品篡改、未知签发者、不兼容 adapter、断电激活、旧制品回退与信任材料过期；失败保留已验证版本并停止不允许的新动作，不恢复已消费审批或过期权限。只升级宿主/Runtime 时，平台和未变更组件无需同步发布，活跃 Run 按兼容规则处理。
- [ ] 对控制面和 worker 分别测试扩缩容、优雅下线、任务 drain、租约抢占、事件重复、后端限流与连接池耗尽。
- [ ] 按 P01—P08 对应的四组范围收口：Knowledge 来源/权限/删除/替换；实验逐样本/引用/回归；Action 参数/审批/未知结果/补偿；Workflow 重启/人工等待/迟到回调/能力受限。每组保存输入组合、执行证据、未支持项和 owner；实验数据量由部署 profile 定义，不把工作簿的示例数量当作平台硬阈值。
- [ ] 对 Knowledge 作业注入解析失败、源权限变化、索引切换中断和删除后缓存迟到；验证旧受权版本或显式不可用状态，不能发布半成品或继续泄露撤销数据。分别替换向量库、Embedding 或 Reranker；证明不需修改控制面和 Runtime、不兼容索引拒绝新绑定且可重建/回滚。
- [ ] 在工具成功而记录未完成、审批成功而命令未投递、记忆迁移中断、外部评估失联等窗口注入故障。
- [ ] 演练备份恢复：Task/Run、审批、委派、release manifest、artifact 元数据及对象、provider binding 和 secret 引用一致恢复。恢复后先暂停新动作，再对账外部 Run。
- [ ] 设置 SLO：状态收敛延迟、任务恢复时间、证据丢失容忍、成本偏差；在线撤权生效与断连最大陈旧授权窗口分别测量，不能宣称分区期间实时撤权。具体值由目标部署压测决定，在发布前固定成可测阈值。资源下限以实际模型部署方式与并发测试为依据，不沿用未经测量的推荐数字。
- [ ] 为 AgentOps 定义运行看板、告警归属、故障分级和处置回执；对后端失联、成本超限、评测退化和审批积压演练“发现→限制新动作→人工处置→恢复验证”。
- [ ] 建立 noisy-neighbor 测试和组织配额；不以单租户吞吐代替多租户验证。
- [ ] 发布自托管部署 profile，按需启用后端，明确网络出口、凭据、日志保留和升级步骤。
- [ ] 按部署 profile 做旁路攻击演练：从 Runtime/插件容器尝试直连工具目标、Memory、对象存储和凭据端点；验证网络/身份/资源策略同时生效，并保存失败请求及网关回执。若企业环境无法施加这些边界，支持矩阵只列已验证的较低治理等级。
- [ ] 合同测试集覆盖内置及已支持的第三方组合；记录 adapter、后端、schema、SDK 版本和测试证据。
- [ ] 在支持矩阵中验证混合语言部署：Python 控制面与非 Python Runtime/网关分别重启、升级、超时、断线和撤销凭据；版本不兼容时拒绝新绑定，远程失联时进入待对账而非假定失败或成功。
- [ ] 为托管后端演练供应商限流、地区不可用、回调迟到、会话无法继续、内部能力变化及用量延迟；断线后按 session/turn 对账。故障切换只路由新 Task；旧 Run 不能无声迁到另一供应商，必要时经人工确认用新 Run 重做或基于已验收产物继续。
- [ ] 做独立发布演练：只升级 Memory、只升级非 Python Runtime、只升级 Gateway/协议 adapter、只切换评估/观测目标；逐项证明无需同步升级其他能力或迁移其私有数据。新旧服务版本并存时检查新 Task 路由、活跃 Run 固定绑定、回退和弃用窗口。
- [ ] 选一个已稳定的候选单元做进程外替代演练：保持调用方用例和契约测试不变，仅替换装配与数据访问 adapter；记录仍需修改的直接 import、共享事务和全局状态，纳入拆分前清理清单。不要求生产环境在此步实际拆服务。
- [ ] 增加 Sandbox 运营演练：租户并发/资源配额、空闲回收、最大有效期、续租授权、预热池隔离与失效、孤儿实例清理和删除回执。按 Run 归属计算、存储及快照成本，缺失用量标记未核实；平台重启后对账真实云资源，不能仅删除本地记录停止计费。
- [ ] 只升级 Sandbox 提供方或 adapter，保持 Runtime/Memory/控制面版本不变；演练创建响应丢失、终止失联、恢复后凭据过期、共享工作区撤权和旧快照访问。记录资源回收时延、孤儿资源数、冷启动/恢复延迟及成本偏差，按部署目标设门槛。

**验收：**故障恢复无越权、无静默丢任务、无已知窗口盲目重复副作用；发布支持矩阵只包含已通过组合。量化指标有实际报告，且告警能指向负责人和处置记录，不能只写“高可用”。进程外替代与独立发布演练通过同一契约测试；只变更一个能力的实现不要求其他能力重发版或迁移私有数据，遗留的跨包直接依赖有明确清理计划。

**迁移/回退：**数据库采用 expand/migrate/contract；先备份和演练，再逐步升级；旧 worker 与新 schema 的兼容窗口明确，回滚不执行破坏性 downgrade。

### step17 — 在需求成立后增加跨组织联邦

**启动条件：**已有独立组织确需协作，且 step16 的内部治理与团队执行通过验证。仅为了展示 A2A 不启动此步。

**操作：**

- [ ] 建立 OrganizationTrustRelationship：对方身份、可信端点、准入证据、允许能力、数据类别、预算、期限和撤销条件。
- [ ] 远程 Agent 登记映射为 federated deployment，内部 Team 的成员记录只引用它，不为其复制本组织管理员身份。
- [ ] 通过 A2A 或其他 adapter 映射平台 Task，保留协议版本和能力；不能假定任意协议均支持强暂停、恢复和工具级审计。
- [ ] 出站交接包执行字段级最小化、数据授权和 DLP；入站结果作为不可信内容解析，不能直接成为新权限或新工具定义。
- [ ] 固定任务合同：输入、产物 schema、完成标准、费用边界、超时、取消语义、回调认证和争议/对账处理。
- [ ] 轮换对方密钥和撤销信任关系后停止新任务；处理已外发数据的保留/删除请求与回执，不能承诺撤销已传输信息。
- [ ] 对远程自报事件标明来源和验证等级，不与平台直接观察到的动作混为同等证据。

**验收：**跨组织任务只泄露批准的交接数据；伪造回调不改变任务状态；对方取消失败或失联时状态和费用边界真实可查。

**回退：**撤销新任务路由，按合同处理活跃任务；保留最小证据与交换记录，不通过删除本地映射假装远程工作已停止。

### step18 — 建立适配器生态和内部资产目录

**前序交付：**最小 schema/客户端样例在 step3 发布，独立发行包、宿主启动指南和业务 App 集成样例在 step5 交付，生产本地安装与更新在 step11 认证。本步扩展多语言 SDK、第三方模板、目录分发和认证流程，不再承担独立 Runtime 的首次可用交付。

**目标：**第三方可以独立接入其他项目，而无需进入 Hecate 内核开发。

**操作：**

- [ ] 将已验证的语言中立 schema、OpenAPI、事件样本和契约测试作为首要接入产物发布；提供 Python 和至少一种非 Python 参考 adapter。SDK 只是便捷封装，不是接入前提，也不安装完整平台及其全部基础设施依赖。
- [ ] 各能力契约分别发布版本、变更记录、兼容范围、弃用期限与符合性测试；允许实现者只依赖所需契约，不安装整个 Hecate，也不因其他能力升级而强制重建自己的 adapter。生成的多语言 SDK 必须与公开 schema 同步，SDK 版本不作为协议兼容性的唯一依据。
- [ ] 为 Runtime、网关和其他进程外服务提供语言无关的符合性测试入口：实现者用本语言启动服务并运行标准请求/事件/错误/控制场景；测试报告固定契约版本、传输绑定和实际能力等级。
- [ ] 提供 Runtime、Memory、Evaluation、Observability adapter 模板、契约测试、能力示例和错误处理规范。
- [ ] 为整体 Knowledge 服务与可选组件化 Knowledge 实现分别提供符合性测试样例；向量库/Embedding/检索组件的对外说明只公布实际需要的契约与能力，不将某个 Python ABC 或默认模型作为所有语言的强制依赖。市场分发仍按外部贡献需求启动；step9 的基本替换测试不得等到本步。
- [ ] 增加 Sandbox adapter 模板与独立生命周期/执行符合性测试，覆盖远程文件引用、命令结果、租约、终止和可选恢复能力；提供方可使用不同语言与基础设施。新增隔离后端按真实客户需求和威胁模型准入，不排定容器、microVM、WASM 的统一升级顺序。
- [ ] 适配器清单记录来源、版本、摘要、许可、实现语言、部署方式、传输绑定、支持契约版本、后端版本、权限、数据流、secret 类型、维护者和符合性测试结果；语言和部署方式不能代替能力认证。
- [ ] 将制品准入与运行能力认证分开记录：包来源/签名/依赖/漏洞/高风险权限由资产审查处理，工具级强制路径、隔离与控制回执由部署符合性测试处理；任何一项过期或失败不得沿用原等级。
- [ ] 复用现有插件信任分层：工作区用户不能任意装载平台进程内 Python 代码；新后端包的安装/升级由平台发布流程执行。
- [ ] 为 Agent/Skill/连接器/adapter 定义统一的注册、准入、安装、绑定、升级、退役生命周期契约；接入内部或外部目录时复用该契约。签名验证身份和完整性，运行能力仍以测试证明。
- [ ] 上述流程不要求 Hecate 自建通用市场：定义中立 manifest、目录查询/解析接口和准入回执，允许连接企业私有仓库、第三方目录及外部签名/扫描/安装服务；内置目录仅作为可替换参考实现。
- [ ] 区分资产与服务：Agent、Skill、工具描述、连接器、团队模板是可版本化对象；registry/catalog、scanner、signer、installer 是生命周期服务。目录不可用时既有部署按已固定版本运行（若策略允许），新版本则不能绕过准入。
- [ ] 让未参与核心开发的实施者按文档完成一个新适配器；将其遇到的内部依赖视为边界缺陷并修复。
- [ ] 只有持续的外部供给与实际使用需求成立后，才单独提出公开市场、伙伴结算与语义市场发现。

**验收：**第三方适配器不修改 Hecate 核心、不安装 Hecate Python 包即可通过进程外协议注册、接受测试、受治理运行并退出；不兼容升级被阻止；用户可替换为非示例项目。Go/Rust 等实现可以依据公开契约接入网关能力，不以新增官方 SDK 为前提。

**回退：**撤销某个 adapter 版本的准入并停止新绑定，活跃 Run 按其固定版本处理；不自动把不兼容配置落到另一个 vendor。

### step19 — 收敛旧路径和可选包，完成退役

**目标：**防止新旧双栈永久共存，重新形成清晰模块边界。

**操作：**

- [ ] 用入口清单证明 REST/MCP/A2A/IM/定时/评估执行已迁到平台执行接口，再移除重复执行循环。
- [ ] 将内置 Runtime 的具体平台适配移到 execution adapter/composition，清理靠函数内 import 维持的业务反向依赖。
- [ ] 核对 step5 已交付的独立 Runtime/宿主和兼容导入使用量，删除达到退出条件的旧 shim、重复装配和延迟反向依赖；不能把独立包的首次交付重新推迟到本步。保持已发布的契约/状态格式兼容，不为清理重写 checkpoint。
- [ ] 减少核心默认依赖，Memory/RAG、专属评估、推理管理和行业能力按 profile 启用；保证标准自托管 profile 仍可直接启动。
- [ ] 删除无调用的占位实现和静默 fallback；保留明确的 unsupported 行为和升级说明。
- [ ] 对退出核心的已交付功能提供可选包或支持终止说明，不突然移除 API 和历史数据读取能力。
- [ ] 依据第六节的已实现能力复核结果，先移除核心对可选实现的直接依赖和默认挂载，再考虑删除代码；对仍有使用量的能力提供 adapter、安装 profile、数据导出及兼容期限。
- [ ] 更新 AGENTS.md、架构图、扩展文档、集成指南、目录及 roadmap，删除旧 EnginePort/Pregel 唯一执行入口等过期描述。
- [ ] 对每项退役记录替代路径、使用量或调用检查、迁移工具、回退窗口和删除条件。

**验收：**新后端无须导入内置 Runtime；核心 profile 不强制安装退出核心的产品能力；旧 API 的兼容与终止状态有明确测试和说明。

**回退：**先停止新使用并观察，再删除；退役版本的代码或包可恢复，但不得回退已经收窄的安全边界。数据删除独立审批，不能作为代码清理的副作用。

## 六、feature-catalog.md 调整映射

本节是 step2 的具体输入，既包含未实现功能的重排，也包含已交付功能的边界复核。所有现有 ID 保留；新能力先在 proposal 中核对空闲后缀再分配，禁止为迁移重排现有编号。

### 已实现能力的边界复核清单

下表中的“移出核心”是架构建议，不等于立即删除。判断依据是 Hecate 应稳定提供企业治理契约，而具体执行算法、编辑器、训练器和供应商适配可由内置参考实现或外部服务承担。功能目录中的 ✅、代码存在、默认安装是三种不同事实；实施前按 step1 复核实际调用和部署使用量。[Microsoft Agent 365](https://learn.microsoft.com/en-us/microsoft-agent-365/guidance/why-agent-365-for-enterprise) 的身份、观测、治理、安全和生命周期能力由多个现有企业服务支撑；这支持“平台保证结果、具体服务可替换”的方向，但下表的处置仍是对 Hecate 的设计判断。

当前 [pyproject.toml](../../pyproject.toml) 的基础依赖仍包括 `hecate-ops`、`hecate-llm` 和 `hecate-sandbox`；[main.py](../../src/hecate/main.py) 直接挂载评测、自优化和工作流等路由。相对地，`hecate-memory` 的路由已按安装情况延迟挂载。因此“已经拆成 workspace 包”和“可选安装、可替换”不能画等号，step1 应实际核对依赖与启动行为。

| 已实现能力与代码证据 | 平台应保留的责任 | 建议处置及可执行动作 |
|---|---|---|
| Pregel 执行引擎、ReAct/图调度（1.3.1 等；[runtime](../../src/hecate/runtime/pregel.py)） | Task/Run、权限、命令状态和治理事件 | **可独立消费的参考 Runtime**：step5 交付共享装配、独立包及宿主，平台通过 `AgentExecutionBackend` 使用；step8 用异构实现验证可替换性，通用接口不暴露 Pregel Graph/Channel；step19 只清理兼容层与遗留依赖。 |
| 多层 Memory、反思/融合排序、RAG 解析/爬取及多向量库（4.x、3.1.x—3.2.x；[hecate-memory](../../packages/hecate-memory/README.md)） | ProviderBinding、ACL、来源、删除/导出及数据驻留 | **可选能力后端**：保留现有包作默认实现；step9 用另一 provider 通过相同治理测试，step19 检查主应用是否仍直接依赖具体排序、解析或存储类。 |
| 内置评估器、AI 合成数据集、在线/离线任务及报告（7.2b—7.4；[evaluation](../../src/hecate/ops/evaluation/engine.py)） | 统一结果、数据集/评估器版本、发布门禁及证据引用 | **可替换评测实现**：step10 将评分、样本生成和报告计算置于 `EvaluationBackend` 后；保留内置实现可选，门禁只消费版本化结果。 |
| 模型微调、模型目录/生命周期及推理管理（6.6、6.44—6.47；[hecate-llm/hub](../../packages/hecate-llm/src/hecate_llm/hub/fine_tuning.py)） | 允许的模型/提供方、凭据、数据范围、预算和部署绑定 | **外部集成或可选 Model Hub**：停止把训练、推理基础设施管理作为控制面必备能力；step2 复核使用量，step19 将可选 Hub 从核心默认依赖中分离，保留现有 API 的迁移路径。 |
| Prompt 自优化与 Skill 自演化（6.19、1.3.6f；[prompt_optimization](../../src/hecate/ops/prompt_optimization/service.py)、[self_evolution](../../src/hecate/studio/self_evolution/pipeline.py)） | 候选制品、评估、审批、版本和发布后审计 | **可选实验能力，先暂停核心扩张**：step11/15 固定“生成候选→评测→人工或既定门禁发布”；step19 依据使用量决定独立包、外部优化服务或停止维护，不能自动改生产权限。 |
| Pregel 专属可视化工作流编辑器和 Graph DSL（1.1.2、1.1.3；[canvas](../../web/src/components/workflow/canvas-area.tsx)、[graph_dsl](../../src/hecate/studio/workflows/graph_dsl.py)） | Agent 定义、版本、制品清单、测试与发布接口 | **内置 Agent Engineering 工具**：保留对现有用户的编辑能力；step11/18 使外部 Agent 能不经 Graph DSL 登记与发布，编辑器不成为平台接入前置条件。 |
| 浏览器、搜索、代码等内置执行工具（5.1、6.27；[search](../../src/hecate/tools/tool/search/factory.py)、[browser](../../packages/hecate-sandbox/src/hecate_sandbox/browser/session.py)） | 工具登记、策略、审批、隔离等级、动作回执 | **可选工具/环境组件**：step7 保证网关强制策略，step18 允许企业工具替换；step19 将高风险执行器按 profile 安装，不因不用浏览器或搜索而缺失控制面。 |
| 插件打包、安装和目录（5.5b；[installer](../../src/hecate/core/plugin/installer.py)） | manifest、供应链准入、权限、版本绑定、停用与追责 | **可替换生态服务**：step18 保留中立清单/验收接口，允许外部包仓库、扫描和安装服务；内置安装器作为参考实现，暂不扩张为通用商业市场。 |
| Slack/飞书等即时通信渠道（11.9 等；[channel packages](../../packages/channels/hecate-channel-slack/README.md)） | 入口身份映射、授权上下文、Task/Run 关联和审计 | **渠道插件**：已按包拆分，step5/19 核对主应用是否仍强制加载渠道 SDK；渠道消息不得成为独立任务或授权真源。 |
| SIEM Webhook/Syslog/OCSF 输出（8.7；[siem](../../src/hecate/ops/siem/exporter.py)） | 不可采样治理事件、访问控制、保留和导出契约 | **可替换证据导出器**：step10 保留统一事件 envelope，按企业目标选择导出格式/服务；step19 检查格式实现是否仍需随核心默认安装。 |

优先复核模型微调/Hub、自优化和主应用内置评测：它们有明确的独立产品或算法属性，也可能带来默认依赖与维护负担。随后审查 Runtime、Memory/RAG、工作流编辑器、工具、渠道和导出器的可选安装程度。只有核实调用方、迁移路径、数据兼容和回退窗口，才将某项从 `optional-component` 进一步改为 `retire-candidate`；不因定位调整直接删除已交付能力。

step2 为每个候选留一条决策记录：Feature ID、代码/数据 owner、当前安装与路由挂载方式、实际调用证据、目标边界和替代接口、兼容与迁移方案、回退窗口、最终处置。若使用量未知，结论写“待验证”，不以个人偏好直接移除功能。

本轮没有证据支持因某个框架流行或衰退就立即删除已实现能力。可明确停止的是无验收的生产承诺和重复建设；代码删除按 step19 的调用、替代、兼容和数据退出证据进行。#181 已将部分规划转入 research，后续需检查对应默认依赖、路由和发布承诺是否同步收敛，不能将改文档状态视为完成模块拆分。

### 提前并纳入平台核心

| 现有 ID/范围 | 调整后的范围 | 对应步骤 |
|---|---|---|
| 13.1b、11.16、11.17、11.20 | 人类、应用、Agent、Run 与委派身份；统一认证上下文和短期凭据 | step4、step7 |
| 11.19、9.16、6.30 | 平台策略与资源授权；去掉 Ontology 前置；外部判定引擎通过统一契约接入 | step7 |
| 2.10b | 准入、可验证能力、委派限制和撤销；统一信任分延期 | step8、step11、step17 |
| 1.3.11 | 持久化 Task/Run 与异步控制接口，sync/stream 作为其视图 | step6 |
| 2.11 | Team 生命周期、成员和职责，模板只是附属能力 | step12 |
| 13.15、2.6 | 持久化跨 Runtime 协作；移除预绑定 ZMQ/gRPC 的要求 | step13 |
| 1.1.24、1.1.23 | 跨后端人类输入、审批、控制状态和任务工作台 | step7、step14 |
| 8.1d | 跨 Agent 关联 ID 与 trace context；不再等待 P5 | step6、step10 |
| 6.21、6.24、9.9 | 决策/授权/产物证据链与审计查询，去掉知识图谱前置 | step6、step10 |
| 8.10、8.12、13.17、13.1a、7.5 | 统一评估结果驱动的准入、发布、灰度、回滚 | step10、step11 |
| 9.5a、6.8-EF5、9.6 | 数据分类、最小化、保留和证据导出；法规映射按适用场景维护 | step7、step10 |
| 9.11、9.17 | 确定性检测与授权处置优先；AI 风险建议不可覆盖硬拒绝 | step15 |
| 13.4、13.4b、9.16a、13.1 | 先验证控制面/worker 多副本和恢复；SaaS profile 在安全和隔离验收后推出 | step16 |
| 5.8、3.3.4 | 企业工具/知识集成和权限传播优先，不建设通用 ETL 产品 | step7、step9 |
| 1.2.1、1.2.2、13.14 | 提供接入契约/最小客户端与独立宿主集成样例；不要求 SDK 用户采用内置编排 DSL，后续扩展生态 SDK | step3、step5、step8、step18 |
| 5.13、12.0、14.1 | 内部可信目录、供应链与受权限约束的资源发现提前；公开商业市场延后 | step11、step18 |

### 保留，但转为后端或集成能力

| 能力范围 | 调整方式 |
|---|---|
| Pregel、compiler、channels、context chain、durable compaction、内部 replay | 内置 Runtime 的优势能力；经 capability 暴露，不要求其他 Runtime 复制 |
| 2.13 ACP | 执行 adapter 之一；A2A、HTTP、SDK、RPC 各按实际后端选择，不规定通用契约只能走 ACP |
| 2.5、2.5a、1.3.18 高级编排 | 选人、协商和规划策略插件；先有团队授权和可靠任务，再开放动态协商 |
| 4.x、3.5.6、3.5.13、3.5.14、6.37 | 内置 Memory 或外部 provider 能力；平台负责绑定、共享、来源、删除和退出 |
| 7.x 评分计算、7.2a-OE8/OE9 | 外部 EvaluationBackend 或 builtin evaluator；共同输出版本化结果 |
| 8.9-O8、8.9b-OE5、8.6-O9 | 优先集成现有观测/事件系统；Hecate 保留任务干预与治理必需界面 |
| 6.5、6.6、6.14、6.44—6.48 | 已有管理适配按可选后端维护；保留准入、凭据和使用治理，不继续扩张训练/推理运维产品 |
| 3.1.x、3.2.x、3.3.x、3.4.x | 由 KnowledgeProvider 提供解析、检索和索引；平台强制来源和 ACL 语义 |
| 6.27、6.27a | 浏览器与 Computer-use 作为受控执行工具/环境；不让其绕过统一治理 |
| 6.32、6.32a、6.40、6.41、13.4c、13.19 | 归入可替换 Sandbox/隔离与网络能力；先完成环境契约、真实远程后端、租约回收与资源治理，再按威胁模型增加组合；不设后端升级链。生命周期、持久工作区、模板/快照治理和资源对账分别列验收，不能仅以某隔离引擎安装成功标完成 |
| 11.4、11.5、11.6、11.9、11.10、11.11、11.11a | 渠道及多模态适配；优先实际企业使用的入口 |
| 1.1.7、1.1.11、6.16、1.1.13 | 配置/计划候选生成，不可绕过准入、版本和发布门禁 |
| 1.3.6f、6.19 | 保留已实现学习与优化，产物经相同评估和审批发布，不授予自我提权 |

### 移出核心承诺或延后

| ID/能力 | 处置 | 重启条件 |
|---|---|---|
| 6.15、7.8 Agentic RL | 转外部训练/优化集成，不自建训练框架 | 有真实模型优化任务、数据授权、评估和算力预算 |
| 6.38 PDDL/MCTS | planner/Runtime 插件，不是平台核心 | 可证明优于现有策略的业务基线 |
| 6.39 工具自动创建 | 如保留，改为候选生成→沙箱验证→人工发布 | 有重复工具开发需求及维护责任人 |
| 6.20、6.22、3.5.1—3.5.12 的完整 KG/Ontology 产品、6.42、6.43 | 行业扩展或外部集成；治理所需资源关系模型不受影响 | 有行业客户且外部图谱服务不能满足需求 |
| 6.28、6.29、6.34、5.11 | 示例 Agent/生态包 | 明确目标场景即可贡献，不扩大内核 |
| 2.12 AP2 | 暂缓 | 有经过授权的交易场景、责任和结算边界 |
| 12.5、6.36、公开 Template/Prompt marketplace 商业能力 | 暂缓，内部目录保留 | 已有稳定资产供给和使用需求 |
| 11.12、11.14、11.15、11.2 full、独立语音产品 | 优先通过渠道和嵌入 SDK 实现 | 已有企业客户要求且现有渠道无法满足 |
| 6.25、6.26、6.33 的大型对象/场景仿真 | 行业扩展；基础测试沙箱与后端可选 fork 保留 | 有具体仿真模型和验收数据 |
| 6.35、6.31 大型工业数据/通用集成平台 | 连接器优先，不自建通用数据平台 | 可交付的行业项目和明确维护范围 |
| 9.6a 等具体法规包 | 按适用范围与专业审查推进，不作为泛化认证承诺 | 明确部署地区、用途和责任主体 |

新增缺口不藏在原条目的“enhancement”长段落中：执行后端契约、Deployment/Task/Run、能力认证、ProviderBinding、平台治理事件、Team/Membership/Delegation、干预命令、退出迁移必须有独立验收条目。本轮实践复核另要求把工作负载身份与委派链、不可旁路的动作/数据面、协作共享再授权、扩展资产风险分级列为可独立验证的验收项；托管执行还需单列 harness/环境双轴登记、供应商会话对账、驻留/保留准入和受控工具回执。能归入已有 Feature 的不重复造编号，确有新责任才追加未使用后缀，具体在 step2 分配。

独立消费新增映射同样进入 step2 的清单迁移，不能只在研究正文保留：

| 可验收责任 | 归属与首次交付 | 范围限制 |
|---|---|---|
| 独立 Runtime 发行包、共享装配与执行宿主 | 内置参考实现；step5a—5c | 可独立消费不意味着其他后端必须安装该包 |
| 本地定义/依赖装配与最小执行制品 | 中立 manifest + 后端专属内容；step3/5，step11 完善发布 | 不把平台 ID、Pregel DSL 或自定义归档格式变成跨后端要求 |
| 本地可靠任务、业务身份/策略、审批与证据 | 治理语义 + 可替换本地 adapter；step6/7/10 | 不强制部署集中 IAM、评测后台或管理表 |
| 受管注册、状态投影、限时授权与重连 | 管理平台/宿主接入契约；step4/6/7 | 不追授历史审批，不迁移活跃 Run，不承诺断连实时撤权 |
| 本地安装、升级/回退与网络模式认证 | 制品生命周期及符合性；step11/16 | 完全隔离网络单独认证；不是无控制面启动的别名 |

上述条目在 feature-inventory 中分别记录已支持 profile、证据引用和未支持能力；沿用既有字段，确需结构化 profile 字段时再升级 schema，不用单一 `delivered` 同时表示技术预览和生产支持。车商库存管理本身不新增为 Hecate Feature。

`Problem_Lab.xlsx` 的 P01—P08 是验收映射的来源 ID，不是 Hecate Feature ID。step2 更新 catalog 和 roadmap 时按下表核对已实现条目及缺口；处置栏是平台职责，不能把实验题目逐项变成内核 Feature。

| 工作簿问题组 | 目录与路线图应增补的可验收责任 | 保留为可替换实现或实验资料 |
|---|---|---|
| P01、P02 | Knowledge 服务/组件两种接入模式；来源版本、同步/索引水位、授权过滤和删除传播；阶段可见性声明 | 测试语料、解析/切片/Embedding/向量库/重排算法 |
| P03、P08 | 实验快照、逐样本与分组评测、引用核验、质量门禁和运行失败分类 | Golden Dataset、grader、trace UI、报表计算 |
| P04 | 可选图检索能力声明、图制品/来源/ACL 与同条件比较 | 业务 Ontology、图构建与实体消歧产品；无需求时保持研究状态 |
| P05、P06 | 工具 schema/错误/回执、文档/派生索引 ACL、委派/审批与安全负例 | 具体业务工具、企业 IAM/策略实现与攻击样例 |
| P07 | Agent 子任务与确定性流程的状态/回调契约、授权补偿及真实恢复能力 | Finance Settlement 案例、特定业务状态机和 planner |

验收状态区分**平台契约通过**、**某实现通过**与**某实验完成**。例如已有 VectorStore ABC 和适配器证明存在替换起点；只有替换后仍通过租户、ACL、删除、索引兼容与独立发布测试，才能将相应部署组合列为已支持。工作簿中固定数量的语料/模型/失败样例是该实验的配置，不是平台级容量或适配器认证门槛。

## 七、roadmap.md 重排后的交付门槛

### 迭代切片与首次交付

下面是实际实施顺序，step 是工作包，迭代是可验收切片。每次只承诺当前范围；未取得第三方账号或尚未通过认证的托管组合不阻塞内置/自托管闭环，但该组合保持未支持，M-A 全部完成前不得宣称三类后端已统一支持。每个切片至少指定实现 owner 和验收 owner，由用户安排实际人员，不在本方案虚构工期。

| 迭代 | 工作包切片与 PR 边界 | 独立可验收结果 | 本轮不扩张的范围 |
|---|---|---|---|
| I-A：基线增补与边界收口 | step1 保留 G1/G2 修复证据并补独立消费基线；step2 的 G5/定位/依赖图分文档与工具 change；登记 P/SC 映射 | 原问题不重复修复；依赖闭包、部署拓扑与 SC 规格明确；清单更新不丢证据 | 不引入新 Runtime、不整体移目录 |
| I-B：契约与共享装配 | step3 草案和真实非 Python 无副作用验证；step5a 共享装配；step4 增量表可独立推进 | 公共契约不依赖 Pregel/平台 ORM，旧服务能调用共享执行逻辑；平台映射与宿主依赖分开 | 不授予生产写入、不冻结全部高级能力 |
| I-Ba：独立执行技术预览 | step5b/5c 的 wheel、宿主、最小本地校验与只读 SC；并行 step5d 单入口平台兼容 | 无源码/管理服务/平台管理表环境独立安装冷启动；业务 API 读取和越权拒绝有本地证据，RAG/Memory 可不安装 | 不承诺生产写入、长任务恢复或受管断连保障 |
| I-Ca：独立产品可靠闭环 | step6 本地持久化、step7 本地 Action/审批、step10 最小证据/评测结果、step11 本地准入/更新、step16 SC 故障集 | 无控制面执行测试任务、等待本地审批并产生可核验写入；重启恢复/对账，制品校验和回退生效；App 可用公开接口观察干预 | 不要求平台 Task 表、管理 UI、企业 Team 或完整 Knowledge 服务 |
| I-Cb：受管任务闭环 | step4/5d 平台接入、step6 投影/控制命令、step7 限时授权及重连、step11 中心发布、step14 最小平台界面、step16 断连集 | 同一宿主连接平台，身份与版本绑定清晰；断连按期限收窄、重连不重复写；人类可见真实状态并阻止可控的新动作 | 不把独立授权模式作为断连 fallback，不假定所有远端可强制取消 |
| I-D：真实外部执行认证 | step8 拆为自托管异构 Runtime、托管 harness、远程 Sandbox 三个接入 change；沿用 I-Cb 门禁 | 每个组合独立报告数据流、身份、受控动作、事件缺口、取消/终止和恢复限制；完成组合后再冻结对应契约 | 不为了统一而伪造取消、回放或托管内部工具控制 |
| I-E：独立能力替换 | step9a/9b 与 9c/9d 分别交付；step10 可并行，step11 扩展组合发布，step16 验证单能力升级 | Memory 与完整 Knowledge 服务分别替换；组件化 Knowledge 更换向量库及另一检索/Embedding 组件，知识接入/删除/ACL、逐样本与阶段评测通过；评估/观测独立替换，不触发全平台发布 | 不先做通用在线双写、全部 provider 组合或自研图谱产品 |
| I-F：企业团队与人类监督 | step12 固定团队；step13 持久委派/验收；step14 完整工作台；逐项 step16 | 异构 Agent 完成交接，成员变更不扩权，人工干预有真实回执，团队收益与单 Agent 基线可比较 | 不默认动态无限组队、不以供应商 subagent 替代企业成员模型 |
| I-G：运营与有限自治 | step16 收口当前生产 profile；有可靠处置动作后再做 step15 | 恢复、负载、独立升级、旁路验证与支持矩阵成立；可先人工运营，再开启有边界自动处置 | 不以尚无客户 SLA 阻塞技术阈值，但不承诺未经压测的 SLA |
| I-H：按需求扩生态 | 满足条件才做 step17/18；step19 从 I-B 起按迁移范围持续执行 | 按真实需求接入外部组织/开发者，旧路径有明确终止和数据退出记录 | 不默认建设公开市场、结算或行业平台 |

I-C 是 I-Ca 与 I-Cb 的集合：车商 App 可在 I-Ca 对应 profile 通过后集成，不必等待 I-Cb。I-D 使用 I-Cb 的管理平台治理门禁；I-E 的绑定和评测服务可在所需契约稳定后与 I-D 分别推进。I-F 只依赖最小发布门禁、已认证的必要后端及实际使用的数据边界，不依赖所有可选提供方完成。I-Ca 由 App 或测试客户端消费本地 command/approval API，I-Cb 提供平台最小视图，I-F 再扩团队观察界面。独立生产支持与完整平台能力是分别验收的交付范围。

**首轮 change 拆分建议（顺序执行，不一次建完）：**

| change | 前置与具体落点 | 必须交付的证据 |
|---|---|---|
| `standalone-consumption-baseline` | 当前已完成 step1 的增量；research 基线 + SC manifest/fixture | 依赖闭包、模式/owner 矩阵、每个 SC 的预期与责任步骤；不声称目标已实现 |
| `governance-platform-positioning` | 上述差距表；step2 ADR/功能清单/roadmap；G5 工具修复单独 PR | 包边界、profile、Feature 映射一致，历史状态不丢失 |
| `execution-backend-contract` | step2；schema、标准样例及窄范围非 Python 验证 | 通用请求/事件不要求平台 ORM 或 Pregel；本地 manifest 可被独立解析 |
| `runtime-shared-assembly` | step3；step5a 的 workflow/composition 依赖整理 | 原执行服务通过共享装配，行为兼容；平台查询没有进入内核 |
| `runtime-standalone-distribution` | 共享装配；step5b/5c 的包、宿主、最小文档与 SC | wheel 干净安装、无控制面冷启动、只读授权与本地证据通过 |
| `standalone-durable-actions` | 独立技术预览；step6/7 本地切片，必要时再拆存储与治理 PR | 重启/审批/未知结果/重复提交/证据失败测试通过；高风险仍按准入开关控制 |
| `standalone-release-conformance` | 本地可靠动作；最小 step10/11 与 step16 | 评测回执、制品校验/更新/回退、数据流及支持矩阵齐备，授予限定独立生产 profile |
| `managed-runner-enrollment` | step4 模型与 step5d 完成，受管 step6/7 切片 | 注册/投影/命令、断连过期/重连、ownership 与中心发布通过；授予限定受管 profile |

每个 change 开始前指派实现 owner 和验收 owner，引用相应 SC/P/S 场景及现有修复证据。若一个 change 同时修改独立存储与授权边界，先拆兼容契约/存储，再实现策略/审批，再启用 profile，避免将基础设施迁移和生产放量绑在同一 PR。上述名称是建议，不表示本次已创建或授权实施。

每个迭代的共同退出记录包括：基线/PR、迁移前后状态、支持的入口和部署组合、确定性负例与评测报告、故障与回退结果、未支持能力和接手 owner。没有真实业务数据时继续用技术基线；新增行业能力、训练/优化器或商业市场则需其具体需求和效果证据。

### 阶段能力门槛

| 里程碑 | 覆盖步骤 | 退出条件 | 尚不承诺 |
|---|---|---|---|
| M-S：独立执行交付 | step1 增量、step2/3、step5a—5c；生产另需 step6/7 本地切片、最小 step10/11 与 step16 | I-Ba 证明干净安装和独立只读运行；I-Ca 另证明本地身份/审批、持久恢复/对账、制品更新与证据留存；分别授予技术预览和生产支持 | 完整管理平台功能、未单独认证的完全隔离网络/Python 嵌入方式；RAG/向量库均非前提 |
| M-A：跨执行后端的治理闭环 | step1—step8，辅以最小 step10/11/14 和分段 step16 | 先交付 I-Cb 的 builtin 受管闭环，再让自托管异构 Runtime 及真实托管 Agent 服务通过相同任务、权限、审批、发布与事件契约；托管组合有数据流/驻留判定、受控工具回执和会话对账，接入等级有旁路与隔离测试证据 | 任意后端无损状态迁移、跨企业或供应商内部动作的强制控制 |
| M-B：可替换企业能力与发布 | step9—step11，按范围执行 step16 | Memory、完整 Knowledge 服务及组件化检索可替换；知识来源/索引/ACL/删除有证据，评测/观测可替换且发布组合固定；不同检索组合的逐样本与分组比较、引用正确性及失败定位通过；托管条件变化可重新准入，敏感 trace 受控；至少一个 Memory 和一个 Knowledge 组件可独立升级 | 所有后端高级能力完全一致、供应商内部状态可完整重建、所有外部 RAG 均提供内部阶段 trace |
| M-C：企业内部团队协作 | step12—step14 | 跨 Runtime 团队完成任务，人类能够可靠观察和干预；成员变化不会继承私人 Connector 或越权读取共享数据 | 自由组队的无限 Agent 社会 |
| M-D：可运营、可恢复的平台 | step16 贯穿前述阶段；step15 单独准入 | 当前交付范围的故障/恢复/负载/隔离通过支持矩阵；若启用自治理，另证实自动处置有界 | 未验证部署组合与泛化合规认证；不默认要求自治理开启 |
| M-E：联邦与生态 | 条件性 step17—step18 | 独立组织可按约定协作，外部开发者可独立贡献 adapter | 公开市场、商业变现必然成功 |
| M-R：持续收敛 | 分批 step19 | 新旧路径有退役证据，依赖和文档保持一致 | 为追求包数量而拆包 |

不提前给完整路线承诺固定月份。step8 完成后，以实际 adapter 工作量、团队容量和生产目标重新排期。外部 Runtime 无法满足治理要求时，先降接入等级或换验证对象，不能为了进度删除治理条件。

## 八、统一验收矩阵

以下是交付门槛，不是建议性测试。按所属 step 逐步落地；本地可控后端用于故障注入，真实托管服务用于验证供应商会话和数据边界，所有副作用写入只使用测试企业工具。

| 场景 | 必须观察到的结果 | 首次落实 |
|---|---|---|
| SC：干净安装与无控制面冷启动 | 不安装完整 Hecate、不挂载源码或建平台管理表，仅按 profile 配置所需 adapter 即启动；管理服务从未连接且不可达仍完成只读执行 | step1 规格；step5b/5c、step16 |
| SC：结构化库存读取与越权调用 | 通过模拟业务 API 读取授权数据域，跨域/伪造角色拒绝；生成建议不触发写入；无 Memory/RAG/向量库也能执行并留证据 | step5c；生产权限在 step7 |
| SC：本地批准写入与重启 | 参数绑定、当前权限与审批均有效才执行；重启后等待/结果可恢复或对账，未知结果不重写；不依赖平台审批服务 | step6/7、I-Ca |
| SC：受管断连与授权过期 | 仅在已授予范围和期限内继续，过期/可信时间不成立按规则拒绝；需在线判定的操作停止，不切换本地自授权 | step7、I-Cb |
| SC：重连与重复控制命令 | 验证当前信任/授权后接收新工作，历史事件去重补传；重复命令/旧 ownership 不重复业务动作，状态投影不覆盖执行事实 | step4/6/7、I-Cb |
| SC：本地审计不可写、中心上传不可用 | 本地不可写停止新受保护动作；中心不可用且本地留存仍符合策略时可缓冲至限额，缺口显式报告；补传只重试证据 | step6/7/10 |
| SC：制品篡改、不兼容与回滚 | 拒绝激活并保留已验证版本，活跃绑定不被改写；回滚不恢复旧授权、已消费审批或执行过的动作 | step5 最小校验；step11/16 完整门禁 |
| SC：默认遥测与外部模型数据流 | 未授权不向中心发送业务内容；模型/工具网络目标与数据类别符合配置，关闭遥测不被误解为禁止所有外部模型请求 | step5/7/10、step16 |
| SC：完全隔离网络配置（条件性） | 本地模型、依赖、制品与有效信任材料齐备，禁止外网时仍通过对应场景；缺一项则该 profile 未支持 | step11/16，启用时单独认证 |
| SC：独立升级 Runtime/宿主 | 无需部署或同步升级管理平台及未变更组件，契约/状态格式不兼容则拒绝激活或先迁移；原 Run 按固定版本处理 | step5/11/16 |
| 已记录调用但结果缺失，或回执存储不可用 | 区分首次调用与已尝试未知结果，受保护写入停止并待对账；不能将无结果当作从未执行 | step1 G2、step6 |
| 成功动作恢复、同动作键参数变更、并发领取 | 恢复真实授权结果引用，参数变化冲突，领取具原子性；不以占位文本冒充结果 | step6—step7 |
| MCP viewer 修改 Agent 或直接执行受保护工具 | 与 REST 等入口相同授权结果；服务端可信上下文及资源边界不可省略 | step1 G1、step7 |
| 普通聊天切换到现有引擎路径 | 实际 HTTP/SSE 多轮调用、拒绝与控制行为一致，旧客户端兼容；默认切换有 G3 验收证据 | step5 |
| 功能清单增量导入与生成 | 手填证据/依赖不丢失，冲突显式失败，受管文档状态不漂移 | step2 G5 |
| P01：仅更换组件化检索使用的向量库 | 保留授权规则与公共查询/引用契约；新后端通过能力、ACL/删除及来源测试，控制面和 Runtime 不修改；不兼容索引显式拒绝或重建 | step9d、step16 |
| P01：Embedding 模型或维度改变 | 版本与索引兼容检查阻止误用旧向量；重建、切流及回退有状态与结果证据 | step9d、step11 |
| P01：比较检索策略 | 在同一知识/ACL 快照上保存版本与逐样本结果；阶段可见时定位召回、融合或重排差异，完整黑盒服务只报告端到端结果与能力限制 | step9d、step10 |
| P02：来源更新、重复、删除或权限撤销发生在接入中途 | 同步游标与索引水位可对账；未验证的半成品不可发布，缓存/派生索引不泄露已撤销内容 | step7、step9c、step16 |
| P02：表格解析或引用粒度不足 | 检查器报告结构/证据位置缺失，引用粒度明确；按任务准入规则拒绝或以低保证能力继续，不伪造精确引用 | step9c、step10 |
| P03：答案正确但引用错误，或检索漏召回 | 两种失误分别计入逐样本及分组结果，关联数据/知识/评估器/组合版本；缺少必须样本不能通过发布门禁 | step10—step11 |
| P04：图检索与向量检索对比 | 图能力按需启用；同一授权输入、数据快照和指标可比较，图证据追溯来源；未启用图实现不影响基础 Knowledge 认证 | step9d、step10 |
| P05：工具参数非法、业务拒绝、远端响应丢失 | schema 拒绝发生在副作用前；三类状态可区分，未知结果待对账且不盲目重试；工具选择失误可关联评测样本 | step3、step6—step7、step10 |
| P07：Agent 提议流程转移并等待人工审批 | 只有业务状态机授权转移；迟到/重复回调不产生第二次效果，后端不支持原 Run 恢复时用关联新 Run，补偿有独立回执 | step6、step13—step14 |
| P08：评测/观测后端失联 | 治理证据和发布拒绝仍可靠，诊断数据缺口显式标记；切换 evaluator 后重新形成可比较基线 | step10—step11、step16 |
| 相同幂等键重复提交 | 返回同一任务/运行关联；不同请求体返回冲突 | step6 |
| 客户端断线/平台 worker 重启 | 可恢复状态与事件订阅；不自动重做未知副作用 | step6 |
| 旧 owner 在租约过期后继续写 | fencing 校验拒绝旧写入 | step6 |
| Runtime 声称支持审批但不能阻止工具 | 无法获得 hosted_enforced 等级 | step7—step8 |
| Runtime 或插件绕过工具网关直连受保护资源 | 网络、身份或资源策略拒绝；若不能拒绝则降级且不宣称工具级强制治理 | step7—step8、step16 |
| 托管 harness 使用远程 MCP 或内部工具直连受保护写接口 | 企业资源拒绝直连；仅经受控网关完成批准动作并留回执，无法阻断则禁用该工具或降低治理等级 | step7—step8 |
| 自托管 Sandbox 接入托管 harness，却宣称整体私有执行 | 部署记录分别显示会话与环境位置；数据分类/驻留门禁按两者真实流向拒绝不合规绑定 | step2—step4、step8 |
| 托管会话创建或取消响应丢失 | 先按供应商 session/turn 与幂等能力对账；状态未知时不盲目重试、不伪报已停机 | step3、step6、step8 |
| Runtime 直连 Memory/产物存储 | 未获得任务/资源授权时拒绝，关键读取留可关联证据 | step7、step9 |
| 审批后修改参数或撤销身份 | 原批准不能继续授权 | step7 |
| 父委派无某项权限，子 Agent 请求该权限 | 拒绝且可追溯委派链 | step7、step13 |
| 外部 Runtime 无法暂停/取消 | 返回能力限制或请求状态，不虚报已停止 | step8、step14 |
| Memory 服务失效 | 与无检索结果区分，按任务策略阻止或降级 | step9 |
| 切换 Memory 后删除或导出 | 来源和映射可验证，删除范围及备份处理有回执 | step9 |
| 观测系统中断 | 核心治理证据仍保存，trace 丢失被标记 | step6—step7；外部替换扩展在 step10 |
| 供应商 trace 延迟、用量缺失或修订 | 平台授权/网关回执仍完整；用量标记为估算或未核实，不能视为最终账单 | step10 |
| 评估结果针对旧 AgentVersion 或缺少必须样本 | 最小发布门禁即拒绝；接入外部 evaluator 后执行相同规则 | step11 最小切片；step10 扩展 |
| 发布回滚时仍有活跃 Run | 只改变新任务路由，活跃绑定不被改写 | step11 |
| 托管供应商能力或数据处理条件变化 | 影响部署停止新高风险绑定并重新准入/评测；旧 Run 按既定策略对账和处置 | step11、step16 |
| 跨 Team/workspace 读取 Artifact/Memory | 没有显式授权则拒绝 | step12—step13 |
| 新增任务协作者后使用发起者私人 Connector | 拒绝，或经过独立的团队授权绑定后再开放 | step12—step14 |
| 供应商内部 subagent 被当作企业 Team 成员 | 不自动生成 Membership 或独立权限；只作为后端执行事件，跨任务委派须重新注册与授权 | step8、step12—step13 |
| 未审查的可执行 Skill/adapter 请求数据库写入或任意 URL | 发布准入拒绝或进入专项审查，不以签名通过替代风险审查 | step11、step18 |
| 人类重复点击或使用过期状态审批 | 幂等处理或冲突，不执行两次 | step14 |
| AI 建议允许硬拒绝动作 | 不覆盖硬拒绝，记录建议与最终判定 | step15 |
| 备份恢复时远程 Run 仍执行 | 先对账再恢复动作，不产生第二个同义执行 | step6/8 配套 step16 |
| 远程自报完成但缺少约定产物 | Task 不直接成功，进入验收/对账 | step17 |
| 安装不兼容或未授权的 adapter | 拒绝安装/绑定，不自动提升信任等级 | step18 |
| Sandbox 创建响应丢失或命令执行结果未知 | 按请求/命令 ID 对账，不盲目创建第二个实例或重复副作用 | step3、step8 |
| Sandbox 销毁或空闲回收 | 已交付产物仍可授权读取；临时数据与持久工作区按各自策略处理，资源释放有回执 | step9、step16 |
| 从旧快照恢复或 fork | 重新检查权限、凭据和数据范围；不恢复已撤销授权，不默认跨租户共享 | step11、step16 |
| 多 Agent 复用环境或共享工作区 | 信任范围及安全配置匹配，敏感残留已清理，共享写入冲突被处理 | step13、step16 |
| 平台重启或与 Sandbox 后端失联 | 状态标为未知并停止新授权；实际资源通过租约/提供方对账回收，不能以删除本地记录代替销毁 | step8、step16 |
| 仅升级 Sandbox adapter/服务 | 其他组件保持原版本；既有环境绑定不被静默改写，不兼容恢复被拒绝 | step16、step18 |
| 只升级 Memory 或 Gateway 的进程外实现 | Runtime、控制面与其他后端保持原版本；已绑定任务不被静默迁移，不兼容新绑定被拒绝 | step9、step16 |
| 多语言 adapter 使用不同契约版本 | 按能力分别协商与测试；支持窗口内可并存，超出范围显式拒绝，不触发全平台同步升级 | step3、step16、step18 |

共同质量指标：任务完成率、人工接管率、越权拒绝正确性、审批生效正确性、重复副作用次数、任务恢复时间、事件缺口、每任务成本及估算偏差。对比多 Agent 与单 Agent 时固定数据集、资源上限和完成标准。

## 九、每个 change 的执行模板

后续实施遵守仓库 OpenSpec 流程。本方案不是一次性执行授权；用户选择某一步后，为该步建立独立 proposal/design/specs/tasks。不要把所有步骤放进一个超大 change。

### 开始前

1. 读取该步依赖的验收证据和当前代码，不从本研究文档推断功能已经落地。
2. 检查工作区和在途 change；通过项目工作流建立独立分支/worktree，不能在 main 修改。
3. 用明确的 change 范围启动用户授权的 propose/apply 流程。`./scripts/opsx-flow.sh start <change-name>` 在 Git Bash/WSL 等可执行 Bash 的环境运行。
4. 在 design 中写明所属子包、公开应用接口、数据/状态写入 owner、跨包调用、兼容窗口、默认开关、失败语义及回退方式。
   涉及执行组件时另列发行包依赖闭包、独立/受管/完整平台模式、网络模式、本地授权与证据责任、离线陈旧窗口、技术预览与生产支持差异。新增 Runtime 扩展点必须写明第二实现或具名消费者。
5. 在 tasks 中逐项列代码、迁移、测试、文档及支持矩阵，完成即更新。

### 拆 PR 的顺序

先契约与兼容类型，再增量 schema 和回填，再 adapter/服务，再 API/UI，再启用和清理。各 PR 保持一个目的；不可在删除旧路径的同一变更中首次上线未验证的新路径。

### 验证与收尾

- 开发期间运行受影响的契约/服务/权限/迁移测试，涉及 web 时执行 `web/package.json` 中适用的检查和构建。
- 每个新增能力都检查跨子包 import 和表写入；不得因为仍在同一进程就绕过公开接口。已有违规依赖按记录的迁移步骤收敛，禁止新增同类依赖。
- 推送前按仓库要求运行 `ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/` 和 `python -m pytest <affected dirs/files> -q`；完整测试套件由 CI 执行。若涉及抽取包，补该包的安装及测试矩阵；检查测试实际收集范围，跳过或扫描空目录不能当作通过证据。
- 运行对应 OpenSpec 验证，保存契约测试和故障场景结果；没有证据不标 production。
- 推送遵守用户显式批准规则，不绕过 hooks。
- 合并后再按用户触发的 archive 流程归档，并检查 positioning、catalog、roadmap 与架构规则是否需要同步。
- 将每一步的状态、change/PR、迁移结果、验收报告和未支持能力写入路线图。研究文档保留决策依据，不承担实时状态源。

按 I-A → I-B/I-Ba → I-Ca 先交付业务 App 可独立消费且有治理、最小评测/发布和人工控制的任务闭环，再经 I-Cb 接入控制面；step3 同时用真实异构后端验证契约，正式外部接入认证在 I-D。之后独立推进 Memory/评估替换与团队协作，按第四节的实际依赖安排，不能把所有后端替换或自治理设为共同前置。

## 十、研究依据及使用边界

下列官方资料支持“跨框架治理、统一登记、身份与任务边界”的方向，但不证明任何产品可以不经适配直接替换 Hecate。所有产品能力在具体选型时应固定版本并重新核验；本方案的模块划分和步骤是对 Hecate 的设计建议。

### 本轮重新核验的实践与架构裁决

本轮选择与架构决策直接相关的官方资料复核，不将前次全部竞品对照表标为已再次验证。结论是多种执行方式会并存，Hecate 应保持可组合的企业治理边界；这是一项设计判断，不是对某种技术未来市场份额的预测。

| 官方实践 | 对 Hecate 的具体裁决 | 对应交付 |
|---|---|---|
| [AWS AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html) 支持不同框架和模型的 Agent/工具托管 | 保留执行后端适配层，内置 Runtime 是参考实现；不以重写内置引擎获得跨框架支持 | step3/5/8 的共同契约与真实替换 |
| [OpenAI Agents API 架构](https://developers.openai.com/api/docs/guides/agents-api/architecture) 将托管 harness 与环境选择分离 | 控制平台 Task/Run 和受保护资源入口；分别认证会话、环境、内部工具和企业网关能力，不把托管运行框架当成只有模型 API | step4/7/8/11 的双轴登记及证据 |
| [Google Agent Identity](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-identity-overview) 将 Agent 身份与鉴权管理、Gateway 和凭据能力连接 | 独立建模 Agent principal、工作负载身份、委派与目标资源授权；仅有用户 JWT 或 Agent Card 签名不够 | step4/7 的身份链和动作授权 |
| [MCP 授权规范草案](https://modelcontextprotocol.io/specification/draft/basic/authorization) 明确受众与 token 使用边界 | 不把协议接通当作企业授权；入口验证目标受众，出站资源使用独立作用域凭据，版本在 adapter 中固定。草案变化不自动成为生产升级要求 | G1、step7/8/18 的协议准入与版本测试 |
| [Anthropic Agent 评测实践](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) 区分轨迹与环境实际结果，评测 harness 与模型组合 | 从首个迭代就检查真实工具状态、权限负例和恢复结果；固定组合及 evaluator 版本；线上 trace 和 Agent 自称成功不能替代验收 | step1 基线、I-C 发布门禁、step10 扩展 |

因此本轮不新增竞品功能大清单，而将可替换边界、真实副作用保证、组合发布和持续评测作为近期投入重点。自建完整训练、通用 IDE、完整推理运维、公开商业市场继续按第六节边界处理。

### 延续使用的参考资料

逐项目对照、官方证据及与本方案的裁决见 [企业 Agent 平台实践对照](agent-platform-practice-review.md)。该对照覆盖本轮指定的企业平台、harness、工作台与应用构建产品，并区分官方产品能力声明和 Hecate 的实施推论。

- [Microsoft Agent 365 overview](https://learn.microsoft.com/en-us/microsoft-agent-365/overview)：将观察、治理、安全及生命周期管理作为企业 Agent 控制面的职责，支持本方案的定位选择。
- [Google Agent Registry overview](https://docs.cloud.google.com/agent-registry/overview)：统一登记 Agent、MCP、Skill、Endpoint 和 Publisher，支持把内部目录与治理绑定。
- [AWS AgentCore FAQ](https://aws.amazon.com/bedrock/agentcore/faqs/)：不同框架/模型与身份、授权委派等平台能力可以分离，支持后端与治理边界的设计。
- [A2A specification](https://a2a-protocol.org/latest/specification/)：任务、消息、产物和取消等协议能力；取消请求并不保证成功，因此平台不能把所有外部后端视为强控制运行时。
- [MCP security best practices](https://modelcontextprotocol.io/docs/draft/tutorials/security/security_best_practices)：令牌、代理与网络访问边界，适用于工具治理和外部资源接入设计。
- [LangSmith OpenTelemetry integration](https://docs.langchain.com/langsmith/trace-with-opentelemetry)：标准观测接入是可替换性的一个路径；不替代平台授权和企业审计责任。
- [LangSmith evaluation types](https://docs.langchain.com/langsmith/evaluation-types)：区分发布前离线评测与生产中的线上评测，支持本方案对两种评测流程分别建模。
- [Pi](https://github.com/earendil-works/pi)、[Mem0](https://github.com/mem0ai/mem0)：仅作为运行时/记忆适配验证的候选示例。平台模型、配置字段、Feature ID 和验收不能绑定这些品牌。
- [OpenSandbox 架构](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/index.md)与[生命周期 API](https://github.com/opensandbox-group/OpenSandbox/blob/main/specs/sandbox-lifecycle.yml)：生命周期与执行接口可独立于具体隔离后端；用于验证 SandboxProvider 的边界，不将其协议或产品指定为唯一实现。
- [Daytona 生命周期](https://www.daytona.io/docs/sandboxes)与[持久化](https://www.daytona.io/docs/en/persistence/)：区分环境暂停/停止/归档和外部持久数据；支持本方案对工作区、快照与产物分别建模。具体恢复保证仍由后端能力测试确定。
- [OpenShell 凭据管理](https://github.com/NVIDIA/OpenShell/blob/main/docs/how-it-works/providers/overview.mdx):网络许可与凭据目标绑定分别校验，支持将凭据代理和环境出口作为独立强制边界。
- [AgentCore Code Interpreter 会话管理](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-session-characteristics.html)：隔离会话、超时与回收说明，支持为环境定义明确生命周期；不将供应商隔离声明自动认定为 Hecate 已验证的部署保证。
- [OpenAI Agents API 架构](https://developers.openai.com/api/docs/guides/agents-api/architecture)与[执行方式对比](https://developers.openai.com/api/docs/guides/agents)：托管 harness 持有模型/工具循环及会话，而 Sandbox 可单独选择；自托管循环与托管循环是不同的执行方式，不应把环境位置当成会话位置。
- [OpenAI Agents API 数据限制](https://developers.openai.com/api/docs/guides/agents-api/overview)、[Sandbox 安全说明](https://developers.openai.com/api/docs/guides/agents-api/environments/security)与[观测/用量](https://developers.openai.com/api/docs/guides/agents-api/observability)：提供当前试点的数据驻留、MCP 连接来源、事件及用量核验依据；这些供应商条件可能变化，选型时须重新核验，不作为平台通用保证。

首次应证明的产品能力是：企业分别用内置、自托管异构 Runtime 和一个真实托管 Agent 服务，结合自己选择的 Memory 与评估系统完成同一受控任务；身份、权限、审批、产物与审计规则保持一致，同时如实标注各后端不能提供的控制和数据保证。通过这个闭环后，再扩大后端数量和生态范围。
