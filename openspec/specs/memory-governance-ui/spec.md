# memory-governance-ui Specification

## Purpose

定义 Studio 记忆治理界面(Memory Center):L3/L4/recall 的浏览与搜索、审计视图(edit log 与整合运行)、策略编辑表单与 archive/恢复操作。界面定位为"浏览 + 软删治理",内容修改走既有 agent 工具路径,UI 不提供直接编辑。

## Requirements

### Requirement: Memory Center 浏览与搜索

系统 SHALL 在 Studio 提供 Memory Center 页面,含三个浏览页签:L3 用户事实、L4 知识记忆、召回对话。每个页签 SHALL 提供列表(分页)与语义搜索框,数据经 `memory-api` 治理端点取得;L3/L4 条目 SHALL 展示内容摘要、重要度、最后确认时间、来源与 namespace scope 标识。

#### Scenario: 浏览 L3 事实

- **WHEN** 管理者打开 Memory Center 的 L3 页签
- **THEN** 页面分页列出本 workspace 的用户事实,含重要度与最后确认时间

#### Scenario: 语义搜索

- **WHEN** 管理者在 L4 页签输入查询词执行搜索
- **THEN** 返回语义相关命中列表并高亮匹配,仅含本 workspace 数据

### Requirement: archive 与恢复操作

L3/L4 条目 SHALL 提供 archive 操作(软删),并提供 archived 过滤视图与恢复操作;对 archived 条目 UI SHALL NOT 提供内容编辑入口,仅可查看、archive、恢复。

#### Scenario: archive 一条记忆

- **WHEN** 管理者对一条 L4 记忆点击 archive 并确认
- **THEN** 该记忆从默认列表消失,出现在 archived 过滤视图中并标注原因

#### Scenario: 恢复 archived 记忆

- **WHEN** 管理者在 archived 视图中对一条记忆点击恢复
- **THEN** 该记忆回到默认列表并重新参与检索

### Requirement: 审计视图

系统 SHALL 提供两个审计视图:记忆编辑日志时间线(操作类型、来源、目标记忆、revision、时间,支持按 agent/来源/时间过滤)与整合/反思运行列表(trigger、窗口水位、状态、统计、失败原因)。

#### Scenario: 查看编辑时间线

- **WHEN** 管理者按来源过滤"consolidation"查看编辑日志
- **THEN** 页面仅显示整合产生的记忆变更,每条含目标记忆与操作类型

#### Scenario: 排查失败运行

- **WHEN** 某整合单元的记忆未按预期更新,管理者查看运行列表
- **THEN** 可见该单元最近运行的失败状态与原因

### Requirement: 策略编辑表单

系统 SHALL 提供记忆策略的编辑表单,支持 workspace 级策略与 agent 级覆盖的新建/编辑;表单 SHALL 即时回显校验错误(未知工具名、越权配置、超上限数值,错误信息来自治理 API);SHALL 展示生效链预览(resolved view):每个字段显示生效值及其来源层级(平台默认 / workspace / agent)。

#### Scenario: 编辑并保存策略

- **WHEN** 管理者在 agent 级表单中收窄工具子集并保存
- **THEN** 保存成功,生效链预览中该 agent 的工具面来源层级显示为 agent

#### Scenario: 越权配置回显错误

- **WHEN** 管理者提交越权的共享上限配置
- **THEN** 表单回显治理 API 返回的明确错误,保存不生效

### Requirement: 治理界面权限

Memory Center 入口与全部治理操作 SHALL 仅对 `editor` 及以上角色可见;`viewer` 角色 SHALL NOT 看到治理入口;actor 私有记忆的展示范围与 `memory-api` 的权限语义一致。

#### Scenario: viewer 不可见

- **WHEN** 仅具备 viewer 角色的用户打开 Studio
- **THEN** 导航中不出现 Memory Center 入口,直接访问治理路由被拒绝
