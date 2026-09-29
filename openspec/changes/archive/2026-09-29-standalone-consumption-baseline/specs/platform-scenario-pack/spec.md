# platform-scenario-pack Delta

## ADDED Requirements

### Requirement: Standalone-consumption scenario group (SC)

场景包 MUST 在 manifest 中维护独立的 SC(standalone-consumption)场景组,条目对齐演进方案 §八验收矩阵的 SC 行(干净安装与无控制面冷启动、结构化库存读取与越权调用、本地批准写入与重启、受管断连与授权过期、重连与重复控制命令、本地审计不可写与中心上传不可用、制品篡改/不兼容与回滚、默认遥测与外部模型数据流、完全隔离网络配置、独立升级 Runtime/宿主)。每个 SC 条目 MUST 声明:场景 ID(`SC<nn>`)、所属运行模式(独立/受管/条件性)、断言摘要、责任 step 及门禁 change;状态为 `planned` 时 MUST 绑定门禁并 MUST NOT 存在实现测试,不得以 skip 或空目录冒充交付。SC 场景随其责任 step 对应 change 交付后才可翻转为 `implemented`,翻转时 MUST 存在与其断言对应的测试;既有 S/P 组条目与 ID MUST NOT 因 SC 组引入而改变。

#### Scenario: SC entry declares gating before delivery

- **WHEN** 校验脚本检查 manifest 中的 SC 条目而其责任 step 对应 change 尚未交付
- **THEN** 条目状态为 `planned`、写明责任 step 与门禁 change,且场景包目录中不存在以该场景 ID 为前缀的实现测试

#### Scenario: SC entry flips only with delivery

- **WHEN** 某责任 change 交付了某 SC 场景的独立运行/受管运行验证
- **THEN** 该条目翻转为 `implemented` 且存在对应测试;仅注册清单或宣称完成而测试缺失时,校验失败

#### Scenario: SC group addition leaves S/P entries intact

- **WHEN** SC 组加入 manifest
- **THEN** 既有 S01—S11 场景条目与 P01—P08 覆盖状态保持不变,SC 组不改变其语义或 ID

### Requirement: SC fixture carries the business rules

SC 场景的模拟库存 API fixture MUST 自身承载业务规则:提供两个相互隔离的数据域、只读身份与需审批的测试写动作;域隔离、角色拒绝与审批门禁 MUST 由 fixture 在业务 App 侧强制执行并以确定性断言验证,未授权读取/写入的拒绝记录 MUST 可查询。库存仅为示例业务域,fixture MUST NOT 将示例业务领域(库存、定价、客户管理等)实现为 Hecate 平台模型,MUST NOT 以 RAG、向量库或 Memory 为前提;结构化业务查询走业务 API,不依赖文档检索。fixture 级测试只验证 stub 自身行为,不声称独立宿主能力。

#### Scenario: Read-only identity cannot cross domains

- **WHEN** 持只读身份的调用方请求另一数据域的库存数据
- **THEN** fixture 拒绝该请求并留下可查询的拒绝记录,两数据域内容互不可见

#### Scenario: Write requires approval inside the fixture

- **WHEN** 需审批的写动作在未获批准时被调用
- **THEN** fixture 不产生状态变更;获批后再次调用时恰好执行一次,重复调用不产生第二次变更

#### Scenario: Fixture stays self-contained

- **WHEN** 检查场景包的 SC fixture 与其测试
- **THEN** 不存在新增的 Hecate 业务领域模型,场景运行不要求安装 RAG、向量库或 Memory 组件

### Requirement: Standalone baseline document binds to the manifest

独立消费基线文档(`docs/research/standalone-consumption-baseline.md`)MUST 引用 `tests/scenarios/manifest.yaml` 作为 SC 场景的唯一事实源(引用而非复制),其能力支持状态登记(MUST 区分 supported / unsupported / unverified)MUST 与 manifest 中 SC 条目状态一致:manifest 标记 `planned` 的场景,基线文档对应能力 MUST 登记为未支持/未验证并写明责任 step,MUST NOT 登记为已通过。该文档 MUST 保持快照性质:链接并引用 `platform-evolution-baseline.md` 的既有证据,MUST NOT 覆盖或重写其结论。

#### Scenario: Baseline doc references the manifest

- **WHEN** 独立消费基线文档陈述 SC 场景清单或验收规格
- **THEN** 它引用 manifest 中的 SC 场景 ID,manifest 变更时基线文档无需手工同步场景明细

#### Scenario: Status registration never claims undelivered capability

- **WHEN** 某 SC 场景在 manifest 中仍为 `planned`
- **THEN** 基线文档将对应能力登记为未支持/未验证并写明责任 step,不存在该能力已通过验收的表述
