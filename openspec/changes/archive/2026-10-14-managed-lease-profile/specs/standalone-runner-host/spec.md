# Spec Delta

## ADDED Requirements

### Requirement: 受管运行装配受管租约 profile

受管模式(control_plane 已配置)下,宿主的治理判定 SHALL 使用受管租约 profile:以经验证的租约事实(签发者、受众、部署绑定、主体/租户、scope、策略版本、签发时间、期限、nonce)结合本地信任配置与本地审批权威完成受保护动作判定,并作为 `PolicyProfile` 的受管实现与本地 policy profile 并存(同一治理服务与安全底线,两套 profile 各自可装配)。受管 profile 及其窗口/版本参数 MUST 来自宿主配置文件与受管信任材料,MUST NOT 经运行期请求修改;断连时受管 profile MUST NOT 退化为把该运行当作本地已授权(本地自授权),readonly 派发沿用既有台账/对账语义不因 profile 切换而收紧或放宽。宿主 SHALL 在凭据交换或 pull 时上报配置指纹(runner profile 与已装载执行定义的摘要),指纹为本地计算值,不由请求参数指定。

#### Scenario: 受管 profile 经配置装配且运行期不可改

- **WHEN** 宿主以受管配置启动,已认证调用方在请求中携带试图改写最大陈旧窗口、期望策略版本或信任根的字段
- **THEN** 服务端忽略这些字段,受管 profile 参数仍由本地配置与信任材料决定

#### Scenario: 断连不退化为本地自授权

- **WHEN** 宿主与控制面断连且租约超窗,受保护动作请求到达派发边界且无有效本地审批绑定
- **THEN** 该动作被受管 profile 拒绝,即使同一工具动作在本地 policy profile 配置下本可允许;readonly 派发不受影响

#### Scenario: 配置指纹随通道上报

- **WHEN** 宿主执行凭据交换或 pull
- **THEN** 请求携带本地计算的配置指纹,平台以该值与操作员登记的期望指纹比对;指纹不匹配时宿主侧新受保护工作停止的语义由平台拒绝投递/授权承载

## MODIFIED Requirements

### Requirement: 生产受管 Lease 绑定完整宿主身份

受管 CLI SHALL 在受保护动作派发时同时校验 lease 的 issuer、host subject、workspace tenant、部署 audience、scope、期限(经可信时间判定)、策略版本(与 profile 期望一致)、陈旧窗口(以签发时间起算)及 nonce；即使签名有效，身份不匹配、策略版本不一致或时间不可信 MUST 拒绝且不调用业务写 API。

#### Scenario: 同签名材料的其他主体或租户

- **WHEN** 当前 Lease 签名有效但 issuer、subject 或 tenant 与已接入宿主不符
- **THEN** 受保护业务 API 调用次数为零且执行失败

#### Scenario: 策略版本不一致零副作用

- **WHEN** 当前 Lease 签名有效但携带的策略版本与受管 profile 期望版本不一致
- **THEN** 受保护业务 API 调用次数为零且执行失败,拒绝原因标明策略版本不匹配

#### Scenario: 时间不可信期间拒绝依赖期限的动作

- **WHEN** 宿主时钟回拨或重启后时间不可信,受保护动作依赖租约期限或陈旧窗口判定
- **THEN** 该动作被拒绝且业务 API 调用次数为零,时间不可信的事实留证据
