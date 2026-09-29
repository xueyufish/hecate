# Design

## Context

- 场景包现状:`tests/scenarios/manifest.yaml` 维护 S01—S11(`implemented`,绑定 `test_s<nn>_*` 测试)与 P01—P08 覆盖状态;`test_manifest_consistency.py` 钉住结构、覆盖与双向绑定;`docs/research/platform-evolution-baseline.md` 按 `_meta.baseline_doc_sync` 义务引用 manifest。`SC` 前缀当前未占用。
- 边界证据现状:`tests/test_runtime/test_runtime_self_sufficiency.py` 是 subprocess 导入探针,只证明 `hecate.runtime` 的导入隔离;`src/hecate/runtime/AGENTS.md` 维护函数内懒加载清单(每行带退出条件)——这是依赖闭包梳理的既有起点,不是安装性证明。`WorkflowExecutionService`、`core/composition/agent_execution_port.py`、`core/composition/runtime_port_adapter.py` 是执行链装配的实际位置。
- 本 change 只交付研究文档与测试资产,不改产品源码;独立宿主、wheel、发行包都是 step5 及以后的交付物。

## Goals / Non-Goals

**Goals**

- 产出 `docs/research/standalone-consumption-baseline.md`,四块内容与方案 step1 增量项一一对应(快照/依赖闭包/三模式拓扑/验收规格与差距表)。
- manifest 追加 SC 组并对齐方案 §八的 10 行 SC 验收场景,`planned` 状态绑定责任 step 与门禁 change。
- 模拟库存 API fixture 以确定性测试验证自身业务规则(域隔离、只读拒绝、审批门禁)。
- 一致性测试扩展:SC 结构语义 + 独立消费基线文档的引用与状态对齐义务。

**Non-Goals**

- 不构建 `hecate-runtime`/`hecate-runner` 发行包、不做干净安装验证(step5b/5c)。
- 不实现任何 SC 场景的宿主级测试;不以 fixture 测试冒充场景交付。
- 不修改 S01—S11/P 组条目、不重跑 G1/G2 验证、不更新 `platform-evolution-baseline.md`(快照不动)。
- 不虚构 owner 与工期:差距表 owner 字段统一"待指派",指派时点留字段。

## Decisions

### D1: 基线文档独立成文,沿用原基线的证据纪律

新增 `docs/research/standalone-consumption-baseline.md`,不追加进 `platform-evolution-baseline.md`——方案明确要求原文件保持快照性质、新文档链接原证据。沿用其约定:基线声明表(分支/提交/在途 change/核验方法)、`【已证实】/【未核验】`标记、`file:line` 以本次基线提交为准。静态核对结论(依赖闭包、wheel 依赖)全部标注"非独立运行测试通过的结论"。

*备选*:扩展原基线 §12。否决——破坏快照不变性,且两个 change 的复核基线提交不同。

### D2: 依赖闭包以"链段 × 分类"二维表落地,懒加载清单为起点而非结论

自上而下按执行链段梳理:启动配置(`core/config.py` 的执行相关段)→ 定义加载(AgentModel/WorkflowModel 查询)→ 图编译(`graph_dsl`/WorkflowExecutionService 组装)→ Worker 与策略 → 身份/策略(`AuthContext`、`tools/policy`)→ 模型/工具(RuntimePort adapter)→ checkpoint(`runtime/checkpoint.py`)→ 证据(EventStore)。每行登记:代码位置、是否执行必需、分类(宿主可注入 / 可选安装 / 待解除耦合)、责任 step、证据类型。`runtime/AGENTS.md` 的懒加载行直接引用并归类(它们全部是"待解除耦合"或"宿主可注入"候选);`runtime/agent_execution_port.py` 已迁 composition 的事实不重复登记。wheel 依赖核对为静态读取 `pyproject.toml`(`[project].dependencies` + workspace 包),与闭包表交叉标注,明确"import 探针通过 ≠ 可独立安装"。

*备选*:写脚本自动生成闭包。否决——step1 要的是带判断的分类与责任归属,自动 import 扫描已存在且恰好是要被降级的证据等级;自动化留给 step5b 的包级构建测试。

### D3: SC 条目放独立顶层键 `sc_scenarios`,不改 `scenarios` 语义

SC 条目与 S 条目的必填字段和测试绑定契约不同(运行模式、责任 step;未来绑定 `test_sc<nn>_*` 而非 `test_s<nn>_*`),放进同一列表会迫使既有校验放宽。独立键让 S/P 校验逻辑原样保留,SC 有自己的枚举与规则。字段:`id`(`SC<nn>`)、`title`(逐字对齐方案 §八行名)、`run_mode`(`standalone` / `managed` / `conditional`)、`status`(本 change 只允许 `planned`)、`tier`(1)、`assertions`、`responsible_step`、`blocked_by`(建议 change 名,沿用方案 §七:`runtime-standalone-distribution`、`standalone-durable-actions`、`managed-runner-enrollment` 等,并在注释中声明名称是方案建议)。`schema_version` 保持 1(纯增量键)。

编号对齐方案 §八:SC01 干净安装冷启动、SC02 结构化库存读取与越权、SC03 本地批准写入与重启、SC04 受管断连与授权过期、SC05 重连与重复控制命令、SC06 审计不可写/中心上传不可用、SC07 制品篡改/回滚、SC08 遥测与外部模型数据流、SC09 完全隔离网络(`conditional`)、SC10 独立升级。

### D4: 库存 fixture 沿用 stub_ticket 惯例,业务规则在 fixture 内强制

`tests/scenarios/tools/inventory_api.py`:`StubInventoryApi` 内存服务,两个隔离数据域(合成库存记录——库存只是示例业务域,fixture 对业务域不可知),身份模型为"只读身份(绑定单域)"与"需审批写身份";操作为结构化查询与审批门禁写。每次调用记录 `(operation, domain, identity, outcome)`——`denied_cross_domain` / `denied_read_only` / `denied_unapproved` 即可查询的拒绝证据。无网络、无模型、确定性。fixture 级测试文件命名 `test_sc_fixture_inventory.py`(函数 `test_sc_fixture_*`):既不匹配 S 绑定正则(`test_s\d+`),也不匹配未来 SC 绑定正则(`test_sc\d+`),不会与场景状态机混淆。

*备选*:把库存规则放进平台策略引擎。否决——方案要求"业务规则放 fixture,业务 API 拥有最终授权",这正是 SC02 要验证的边界(平台策略管动作,业务 API 管状态机)。

### D5: 状态对齐以"登记表 + 行级标记"获得确定性检查

独立消费基线文档的验收规格登记采用固定格式表:每 SC 场景一行,列为 `SC ID | 能力 | 当前状态 | 责任 step`。当前状态枚举固定为 `未支持` / `未验证` / `未支持/未验证`(本 change 不可能有"已支持")。一致性测试据此做两项确定性检查:① 每个 manifest SC id 在登记表出现;② manifest 为 `planned` 时,对应表行状态列不得出现"已支持/已通过"字样。这是绊线(tripwire)而非完备证明,措辞审查仍靠 review。

*备选*:只做文档引用字符串检查(与 `_meta.baseline_doc_sync` 同级)。否决——spec 的"never claims undelivered capability"场景需要可执行断言,纯字符串引用给不了。

### D6: 三模式拓扑沿用原基线 §4 的记法并新增轴

独立/受管/完整私有平台各一节,沿用"实体 → 边"记法;每模式登记:网络模式、可信身份来源(本地信任根 vs 中心签发)、任务/执行状态 owner(方案 §一"状态与信任规则")、凭据来源与数据出口。受管断连行显式写"授权有效期 + 最大陈旧窗口 + 重连责任方",标注为待 step6/7 落地的目标语义而非现状承诺。B1—B5 直连路径不重复罗列,链接原基线 §4。

## Risks / Trade-offs

- [登记表行级标记检查对措辞敏感,文档改写可能误伤测试] → 检查限定为"SC id 行内不得出现已支持类字样"最小断言;表格列结构写进基线文档自身说明。
- [静态依赖闭包漏掉动态路径(反射、wiring 运行时装配)] → 未核验项显式标注,沿用原基线"事实标记"纪律;运行时闭包验证明确归 step5b。
- [SC 编号/门禁 change 名未来调整导致 manifest 与方案漂移] → SC title 逐字取自方案 §八;`blocked_by` 注释声明名称为方案建议,责任 step 变更时先改 manifest 再改基线文档(一致性测试会拦住单向漂移)。
- [fixture 测试被误读为独立运行能力已验证] → 测试 docstring 与 manifest 注释均声明"fixture 正确性验证,不声称宿主能力";spec 场景 `SC entry declares gating before delivery` 钉住 planned 语义。

## Migration Plan

纯文档 + 测试资产:合入即生效,无部署/回滚动作。回退 = revert 单个 PR。manifest 为增量键,旧读取方(仅一致性测试)不受影响;若未来 SC 组需要废弃,删除 `sc_scenarios` 键与对应校验即可,S/P 组不受牵连。

## Open Questions

(无——SC 编号、门禁命名、字段结构均已按方案 §七/§八固定;owner 指派按用户 2026-09-28 决定暂缓,属后续 change 启动时动作,不阻塞本 change。)
