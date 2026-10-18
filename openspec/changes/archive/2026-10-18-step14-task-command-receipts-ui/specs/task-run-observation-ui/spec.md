# Spec Delta

## ADDED Requirements

### Requirement: Task 控制命令回执面板按 Task 查询并分页

任务详情页 SHALL 提供独立的"控制命令回执"只读面板:面板属于 Task,SHALL 经 `GET /api/tasks/{task_id}/commands?limit=&offset=` 查询并展示该 Task 的全部命令回执(按服务端返回顺序渲染),SHALL NOT 随当前选中的 Run 过滤,且在 Task 尚无 Run 时 SHALL 正常工作。分页 SHALL 以 `limit`/`offset` 请求,响应的 `total` SHALL 仅用于呈现总量;空后续页或后续页查询失败时 SHALL 保留返回上一页的入口;手动刷新 SHALL 按当前页参数重新查询。切换 Task SHALL 重置分页与展开状态,并以新 Task 的首页替换当前内容。

#### Scenario: Task 级展示且不依赖 Run

- **WHEN** 某 Task 存在跨多个 Run 的命令,或该 Task 尚无任何 Run
- **THEN** 面板展示该 Task 的全部命令(不受选中 Run 影响),无 Run 时呈现空态而非错误

#### Scenario: 空后续页仍可返回上一页

- **WHEN** 用户进入后续页后,该页因数据变化返回空 `items`,或该页查询失败
- **THEN** 面板呈现空页或错误态,同时保留返回上一页的入口;返回上一页按已访问 offset 重新查询

#### Scenario: 切换 Task 重置分页与展开

- **WHEN** 用户从 Task A(第二页、某行展开)切换到 Task B，随后回到 Task A
- **THEN** 每次切换都从目标 Task 的第一页重新查询，先前的展开行收起，回到 A 时同样请求 offset=0，不恢复旧页码或旧内容

### Requirement: 命令回执五态准确呈现且不跨语义推断

面板 SHALL 按服务端记录原样呈现五种回执状态并映射固定文案:`requested`→已请求、`acknowledged`→已确认接收、`applied`→已应用、`rejected`→已拒绝、`expired`→已过期;`requested` 与 `acknowledged` SHALL NOT 呈现为"已生效"或"已应用"语义。面板 SHALL NOT 依据本地时间或本地计算把 `requested` 改写为 `expired`,过期状态只能来自服务端记录。任一命令的状态 SHALL NOT 改写任务详情页对 Task/Run 生命周期状态的呈现。出现未知状态值时 SHALL 原样回退显示该值,不得臆造文案。

#### Scenario: 五态文案与未知值回退

- **WHEN** 列表分别返回五种已知状态及一个未定义的状态字符串
- **THEN** 已知状态显示对应文案,未定义状态（包括 `constructor` 等对象原型属性名）原样显示其字符串值,不出现"已生效"字样替代 `requested`/`acknowledged`

#### Scenario: requested 不因本地时间显示过期

- **WHEN** 某命令 `state=requested` 且其 `expires_at` 早于浏览器本地时间
- **THEN** 该命令仍显示"已请求",面板不据本地时钟改写状态

#### Scenario: applied 命令不改写 Task/Run 状态

- **WHEN** 某 Task 的取消命令回执为 `applied`,而 Task 生命周期仍为进行中
- **THEN** 命令行显示"已应用",Task/Run 状态区域保持服务端返回的生命周期状态,不因命令状态联动改写

### Requirement: 命令明细可展开且引用按完整身份展示

命令行 SHALL 展示命令类型(`cancel`/`pause`/`resume`/`provide_input`)、回执状态、发起时间与发起者,并支持展开查看 `command_id`、完整 Task 引用与 Run 引用、`expires_at` 及 `expected_revision`;缺失的可选字段 SHALL 显式标注缺失,SHALL NOT 显示为占位事实。Run 引用 SHALL 以完整 `issuer_domain` 与 `id` 文本展示,SHALL NOT 被当作平台 Run UUID 用于跳转或拼接受控链接。

#### Scenario: 缺失值显式标注

- **WHEN** 某命令的 `run_ref`、`expires_at` 或 `expected_revision` 为缺失
- **THEN** 展开区对应字段显示明确的缺失标记,不显示空字符串、0 或臆造引用

#### Scenario: Run 引用不作为跳转链接

- **WHEN** 某命令携带 `run_ref` 为 `{issuer_domain, id}`
- **THEN** 展开区按完整 `issuer_domain/id` 文本展示该引用,面板不提供把它当作平台 Run 详情链接的导航

### Requirement: 命令面板错误守卫、竞态丢弃与面板隔离

命令面板 SHALL 区分加载中、无命令与查询失败三种状态;查询失败 SHALL 消费并展示 `detail.error.{code,message}` 错误结构并提供手动重试,重试 SHALL 按当前 Task 与当前页参数重新发起。面板 SHALL 只接受当前 Task 且当前请求发起之后返回的响应:Task 切换(含 A→B→A 回切)、翻页或刷新后,先前请求的成功与错误响应 SHALL 一律丢弃。命令面板的失败 SHALL 不改变动作面板与事件面板的状态;面板 SHALL 仅发起列表查询,SHALL NOT 调用单条命令读取接口,SHALL NOT 引入轮询或订阅。

#### Scenario: A→B→A 回切不串数据

- **WHEN** 用户从 Task A 切到 Task B 再切回 A,A 的首个首页请求在回切后才返回
- **THEN** 该过期响应被丢弃,面板展示回切后重新发起的 A 首页结果

#### Scenario: 错误只影响命令面板

- **WHEN** 命令列表接口返回错误而动作、事件接口正常
- **THEN** 命令面板显示错误态、`code/message` 与重试入口,动作与事件面板保持既有内容与状态

#### Scenario: 仅发起列表查询

- **WHEN** 用户浏览命令面板的分页、展开与刷新操作
- **THEN** 网络请求仅包含对 `GET /api/tasks/{task_id}/commands` 的调用,不出现单条命令读取接口、轮询或订阅连接

#### Scenario: 切换 Run 保持 Task 命令观察位置

- **WHEN** 用户已进入命令第二页并展开明细，然后切换当前选中的 Run
- **THEN** 命令面板保持当前页码与展开行，不因 Run 切换重查或过滤命令列表
