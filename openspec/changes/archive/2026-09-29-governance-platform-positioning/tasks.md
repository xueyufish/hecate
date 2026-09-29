# Tasks

任务组 1—2 对应 PR1(文档),组 3 对应 PR2(清单工具),组 4—5 对应 PR3(生成与重排),组 6 收尾。PR 间依赖:PR3 依赖 PR2 的 schema v2;PR1 可并行先行。

## 1. 定位与架构文档(PR1)

- [x] 1.1 修复 `docs/design/positioning.md` 中指向 `../research/` 的失效链接为 `../refactor/`;验证:grep 无 `../research/` 残留,相对链接目标文件存在
- [x] 1.2 在 positioning.md 新增"交付边界与部署模式"章节:中立契约与客户端、独立执行组件、企业管理平台三边界表 + 独立/受管/完整私有平台三模式(必须部署、定义/授权来源、首次交付门槛);验证:与方案 §一表格逐项对应,无描述性数字/日期(writing-style)
- [x] 1.3 登记消费者契约章节(中性表述):业务 App 私有化交付示例、技术预览/独立生产/受管生产分级门槛、HTTP/JSON 首服务入口、进程内 Python 嵌入单列认证;验证:不出现具体业务名称,不把示例业务写为平台模型
- [x] 1.4 将供应商托管 Agent 服务列为正式执行后端,声明 harness×环境双轴登记与"企业自托管 Hecate ≠ 所有绑定后端满足私有部署/数据驻留";验证:与方案 §一"执行组合"表口径一致
- [x] 1.5 竞品矩阵一致性修订:仅删除/弱化与既定定位矛盾的表述(引擎中心、Pregel 唯一执行入口类措辞),不新增竞品事实;验证:修订 diff 逐条可指出对应矛盾,新事实为零
- [x] 1.6 更新 `docs/design/architecture.md`:增量新增控制面/执行接入层/可替换后端与信任边界章节、七能力域归属与目标子包、允许的依赖方向与跨包公开接口规则;为 `tests/test_layering_domain.py` 规划包内边界检查(禁止跨子包导入实现/直接写其他领域表);历史例外逐项登记 owner、迁移步骤与退出条件;保留模块化单体与既有模块清单;验证:新增内容引用方案 §三边界表,既有章节内容未被删除,无描述性数字/日期(writing-style,`docs/design/` 非豁免目录)

## 2. ADR(PR1)

> 编号不预占:实施时按 `docs/design/adr/INDEX.md` 取下一空闲号(当前序列尾部为 033,预期为 034/035;若被并行工作流占用则顺延,下述 034/035 仅为指代)。

- [x] 2.1 撰写治理语义 ADR(预期 034)"平台治理语义与执行后端地位":核心治理语义(治理结果保证 vs 实现提供方、内置 Runtime 为参考实现、托管 harness×环境双轴、事件/会话所有权、能力协商、不承诺无损状态迁移);验证:六项决策逐节对应方案 §一/§二,Status 行与决策一致,文件名使用实施时取到的实际编号
- [x] 2.2 撰写包迁移方向 ADR(预期 035)"独立执行组件的包名与目录迁移方向":批准 `hecate-runtime`/`hecate-runner` 包名与目录迁移方向、依赖方向(契约→领域→adapter)、兼容层期限原则;实施归 step5b 的声明;验证:不含未批准的实施细节,与治理语义 ADR 无重复决策
- [x] 2.3 更新 `docs/design/adr/INDEX.md`:两份新 ADR 按主题归类入表,过期计数改为不含数字的表述;验证:INDEX 链接可达

## 3. 清单 schema v2(PR2)

- [x] 3.1 升级 `scripts/feature_inventory.py` 与 YAML 至 schema 2(实施中发现并修复:✅ 写在 ID 格的 46 行此前被静默跳过,解析器现接受两种 ✅ 位置并剥离日期括注;46 条补入清单):新增 `responsibility`/`implementation_mode`/`provider_or_adapter`/`enforcement_point`/`state_owner`/`milestone`/`superseded_by` 字段定义与枚举,研究类条目的 `research_owner`/`hypothesis`/`investment_boundary`/`review_trigger`/`exit_decision`;区分"缺失(欠账)"与"显式不适用(通过)";验证:单元测试覆盖枚举拒绝、n/a 通过、缺失欠账三类
- [x] 3.2 校验器落实 maturity 独立性(delivered 不推导 production)与延期/退役归 status 不混入 implementation_mode;验证:构造 delivered+无 maturity、deprecated+out-of-process 用例断言行为
- [x] 3.3 严格模式增量规则:新/变更条目必须有完整治理字段与 evidence/acceptance;production 声明必须有证据(任何模式);历史条目欠账仅 WARNING;验证:新条目缺 acceptance 严格失败、production 无证据失败、历史欠账非严格通过三个测试
- [x] 3.4 对全量条目运行 extract(merge-by-id)迁移至 schema 2,确认零字段丢失;同步扩写 YAML 头注释的字段所有权说明,覆盖 v2 新增治理字段与研究类字段;验证:迁移前后既有字段逐条目相等(git diff 仅新增空治理字段与头注释)

## 4. 受管生成(PR3)

- [x] 4.1 实现 catalog/roadmap 受管区域标记(`<!-- feature-inventory:managed:... strict="false|true" -->`)与生成器:从 YAML 确定性生成标记内表格,幂等,不触碰区域外正文;验证:连续两次生成字节一致、区域外正文 diff 为空的单测
- [x] 4.2 实现逐字段漂移检测:再生成与当前内容比对,报告到条目 ID + 字段;strict="false" 仅报告,strict="true" 使 check 失败;验证:手工改动受管区域单字段被精确报告,翻转后 check 失败
- [x] 4.3 将清单校验接入 CI(先非严格模式);验证:CI 工作流语法有效,本地模拟运行退出码符合预期

## 5. catalog/roadmap 重排(PR3)

- [x] 5.1 按方案 §六"已实现能力的边界复核清单"逐行落 catalog 处置标注(Pregel、Memory/RAG、评估器、微调/Hub、自优化、编辑器/DSL、内置工具、插件安装器、IM 渠道、SIEM 导出器),对应清单条目填 schema v2 治理字段;验证:逐行勾选对照方案原文,十项全覆盖
- [x] 5.2 落 §六"提前并纳入平台核心"表:新增/调整条目用未占用后缀(以清单 ID 集合核对),登记对应 Feature 映射;验证:新后缀与既有 ID 无冲突(check 通过)
- [x] 5.3 落 §六"保留转后端"与"移出核心/延后"表:处置标注与重启条件入 catalog,对应清单条目状态/字段同步;验证:无条目被删除,状态迁移有 §六依据
- [x] 5.4 新增可验收条目:托管执行准入、供应商会话对账、托管工具强制入口、数据驻留门禁、独立 Runtime 发行包/宿主、受管注册/限时授权/重连、本地可靠任务与制品门禁(独立消费映射行);验证:与独立基线 §6 差距表逐项对应
- [x] 5.5 roadmap 重排:迭代切片(I-A…I-H)与里程碑(M-S…M-R)表替换未来排期,Sprint 1—10 迁入"历史排期"章节;跨后端治理里程碑(M-A)纳入真实托管服务验证条目,不写为任何单一供应商专属能力;验证:未来工作与方案 §七一致,历史 Sprint 可追溯未丢失
- [x] 5.6 研究/实验条目(Research Candidate Pool 等)补生命周期治理字段(允许显式待定);验证:严格模式下研究类条目不再报治理字段欠账
- [x] 5.7 P01—P08 映射的新增平台责任核对 Feature 条目:Memory 与 Knowledge 各自绑定;整体 Knowledge 服务与组件化检索分别认证;来源同步/索引水位/权限删除;实验快照/逐样本结果/阶段诊断;已有能力补验收,不把实验算法另立核心 Feature;GraphRAG、行业流程及完整实验 UI 保持可选/研究状态;验证:逐项对照方案 step2 清单与 §六 P 组映射表,新条目后缀未占用
- [x] 5.8 七类治理能力(身份、策略、调度、审批、发布、证据、资产目录)的治理责任登记:逐项填 schema v2 五元组(`responsibility`/`provider_or_adapter`/`enforcement_point`/`state_owner`/`evidence`),内置实现保留不删除;验证:七类各有清单条目且五元组非欠账,与方案 §一表1"必须保证的治理结果"逐行对应
- [x] 5.9 五能力契约登记(Runtime、Memory/Knowledge、Evaluation、Observability、Gateway):契约 owner、发布单元、状态/数据 owner、当前版本与支持窗口,以受管表落 catalog;明确同进程实现随主应用发布、进程外实现可独立发布,"有 SPI"不等于独立部署能力;验证:五能力各一行且由受管生成接管,漂移检测生效
- [x] 5.10 七能力域(Agent Engineering/AgentOps/Control Plane/Governance/安全/评测/MCP-A2A 接入层)覆盖与缺口标注落 catalog 与 roadmap 的受管区域能力域表;标注能力域标签不等于当前部署单元;验证:与 `architecture.md` 的能力域归属(任务 1.6)一致,受管生成接管
- [x] 5.11 全部受管区域(catalog 处置分类表、契约登记表、能力域表 + roadmap 里程碑表)内容搬迁完成后统一翻转 `strict="true"`;翻转与全部受管内容同 PR,翻转后 check 必须全绿;验证:翻转后手工改动任一受管字段使 check 失败(临时构造后还原)

## 6. 验证与收尾

- [x] 6.1 本地门:`ruff check src/hecate/ tests/ scripts/`、`ruff format --check src/hecate/ tests/ scripts/`、`mypy src/`(仓库标准范围,scripts/ 不在 mypy 路径)、`python -m pytest tests/test_scripts/ tests/scenarios/ -q`(受影响目录,清单工具与场景包);验证:全部 0 错误
- [x] 6.2 `python scripts/feature_inventory.py check`(非严格)0 error 且欠账计数与基线一致;`--strict` 仅在按增量规则应通过的范围运行;验证:两种模式输出均登记到 PR 描述
- [x] 6.3 `openspec validate governance-platform-positioning --strict` 通过;文档链接检查(catalog/roadmap/positioning 内部相对链接目标存在);验证:命令输出无 error
- [x] 6.4 更新方案文档 step2 清单勾选状态并附证据指针;验证:勾选项均有文档/测试/命令证据
