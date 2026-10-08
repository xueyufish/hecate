# Spec Delta

## ADDED Requirements

### Requirement: 受管唤醒全链仅经真实进程验收关闭

受管唤醒链(等待→命令→唤醒→successor attempt→终态→投影)的关闭门槛 SHALL 仅由安装后的 Runner 进程经平台公开 HTTP 控制面 API 与真实网络传输的验收关闭。验收 SHALL 覆盖:受管投递的任务进入持久等待;平台下达 wake 命令(resume/provide_input);宿主经持久命令路径应用命令(一次性 token 消费、新 attempt 入队);successor attempt 执行至终态;successor 关联、执行事件与 effect 回执上传;平台投影仅在宿主事实之后进入终态。业务断言 SHALL 使用真实业务 API 调用计数。组件级或进程内(ASGI)测试可用于开发,但 MUST NOT 关闭本门槛。同一验收套件 SHALL 在配置了 step6 PostgreSQL URL 时于 PostgreSQL 上运行,未配置时 MUST 显式 skip 而非视为通过。

#### Scenario: 等待任务经平台命令唤醒并投影终态

- **WHEN** 受管投递的任务在首个工具后进入持久等待,且平台对该任务下达携带合法输入的 resume/provide_input 命令
- **THEN** 宿主一次性应用命令并在新 attempt 上继续执行至终态;successor 关联先于其执行事件被平台接受,平台投影的终态仅来自宿主上传的事实;原等待 Run 保持自身等待事实,不继承 successor 状态

#### Scenario: 等待期间宿主重启后唤醒仍成立且前序写入零重做

- **WHEN** 任务在持久等待期间宿主进程被终止并重启,随后平台命令到达
- **THEN** 唤醒在新进程生效,successor 执行完成;等待前的受保护业务写入在全链(含重启与唤醒)中恰好调用一次

#### Scenario: 平台重启后命令仍投递且幂等

- **WHEN** 平台在记录 wake 命令之后、宿主拉取之前重启
- **THEN** 重启后命令仍经拉取投递并被应用一次;重复拉取同一 command_id 返回原 applied 回执,不产生第二次业务效果

#### Scenario: effect 回执上传丢失后重试不重复应用

- **WHEN** 宿主已应用命令但 effect 回执上传的响应或连接丢失
- **THEN** 宿主重试上传且平台恰好记录一次 applied effect;命令不会被第二次应用,业务写入计数不增加

#### Scenario: 重复命令与过期命令

- **WHEN** 已应用的 command_id 被再次投递,或超过期限的命令被投递
- **THEN** 重复命令返回原 applied 回执且无新副作用;过期命令被拒绝并留回执,不执行任何业务动作

#### Scenario: PostgreSQL 参数化的诚实跳过

- **WHEN** 未配置 step6 PostgreSQL URL,或已配置并运行同一套验收
- **THEN** 未配置时 PostgreSQL 参数化验收显式报告 skip(不作为通过);已配置时同一组场景在所配置的 PostgreSQL 上执行并如实报告结果
