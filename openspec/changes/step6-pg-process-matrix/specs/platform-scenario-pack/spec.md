## ADDED Requirements

### Requirement: 受管宿主进程矩阵在真实 PostgreSQL 上常态执行

受管宿主进程级场景(租约续停、唤醒链、重连与控制面重启)SHALL 以数据库参数化形式同时覆盖 SQLite 与真实 PostgreSQL:`HECATE_STEP6_POSTGRES_URL` 存在时,同一 CI job SHALL 运行双参数;该变量缺失时 PG 参数 SHALL 显式跳过并如实标注,SHALL NOT 冒充通过。安装后的 Runner 子进程 SHALL 使用与主进程一致的宿主数据库连接(驱动依赖由套件安装步骤提供)。矩阵 SHALL 覆盖:已接受未执行与重投、宿主进程 kill/restart、外部写成功但回执丢失、过期命令零副作用、租约停发后的有界拒绝、控制面重启后的幂等投递。

#### Scenario: Process matrix runs both database tiers in CI

- **WHEN** CI 配置 `HECATE_STEP6_POSTGRES_URL` 并运行受管进程场景套件
- **THEN** 每个矩阵场景以 `sqlite` 与 `postgres` 两个参数各执行一次,PG 参数的宿主数据库连接、动作账本、命令与等待记录均落在真实 PostgreSQL

#### Scenario: Missing PostgreSQL URL skips honestly

- **WHEN** `HECATE_STEP6_POSTGRES_URL` 未设置(本地默认)
- **THEN** PG 参数报告 skip 且原因注明环境变量,SQLite 参数照常执行,报告不把跳过计为通过

#### Scenario: Runner subprocess reaches the service PostgreSQL

- **WHEN** 安装后的 Runner 以 PostgreSQL 宿主数据库 URL 启动并执行矩阵场景
- **THEN** 子进程的连接、账本写入与命令处理全部落在服务容器 PostgreSQL 上,故障注入(重启/停发/丢失回执)后的收敛断言与 SQLite 参数语义一致
