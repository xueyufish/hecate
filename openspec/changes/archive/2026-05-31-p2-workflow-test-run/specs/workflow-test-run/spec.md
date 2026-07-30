## ADDED Requirements — 新增需求

### Requirement: Custom Input Form — 需求：自定义输入表单
工作流编辑器应为测试运行提供输入表单。用户应在点击 Run Test 之前能够指定输入数据（messages 数组和/或自定义变量）。

#### Scenario: Custom input — 场景：自定义输入
- **WHEN** 用户打开输入表单并输入自定义消息
- **THEN** 测试运行应使用自定义消息，而非默认的"test"消息

#### Scenario: Default input — 场景：默认输入
- **WHEN** 用户不修改输入表单直接运行测试
- **THEN** 测试运行应使用默认输入：`{messages: [{role: "user", content: "test"}]}`

### Requirement: Node Output Panel — 需求：节点输出面板
测试运行后，点击画布上的节点应打开一个侧面板，显示该节点的输入数据、输出数据、错误消息（如有）和执行耗时。

#### Scenario: View node output — 场景：查看节点输出
- **WHEN** 用户测试运行后点击一个已完成的节点
- **THEN** 系统应显示面板，包含：node_id、input、output（截断至 1000 字符，可展开）、error、duration_ms

#### Scenario: View failed node — 场景：查看失败节点
- **WHEN** 用户测试运行后点击一个失败的节点
- **THEN** 系统应突出显示 error_message，并显示节点的输入数据

### Requirement: Execution Logs Panel — 需求：执行日志面板
工作流编辑器应在测试运行后显示执行日志面板。面板应显示每个节点的带时间戳日志和执行顺序。

#### Scenario: View execution logs — 场景：查看执行日志
- **WHEN** 测试运行完成
- **THEN** 系统应显示日志，展示每个节点的执行顺序、开始时间、结束时间和状态

### Requirement: Node Status Badges — 需求：节点状态徽章
测试运行期间和之后，画布上的每个节点应显示状态徽章，指示其状态：pending（灰色）、running（黄色）、completed（绿色）、failed（红色）。

#### Scenario: Node status after run — 场景：运行后的节点状态
- **WHEN** 测试运行完成
- **THEN** 每个节点应显示彩色徽章：绿色表示完成，红色表示失败

#### Scenario: Clear status — 场景：清除状态
- **WHEN** 用户关闭测试结果面板
- **THEN** 所有节点状态徽章应被移除

### Requirement: Run History — 需求：运行历史
工作流编辑器应在内存中维护最近 10 次测试运行的列表。用户应能查看之前的运行结果并比较输出。

#### Scenario: View run history — 场景：查看运行历史
- **WHEN** 用户点击"History"按钮
- **THEN** 系统应显示之前测试运行的列表，包含时间戳、状态和耗时

#### Scenario: Load previous run — 场景：加载之前的运行
- **WHEN** 用户点击历史中的某次运行
- **THEN** 系统应显示该次运行的结果（节点输出、日志）
