# Design

## Context

`hecate_runner/evidence.py` 的 `EvidenceStore` 是按天滚动 JSONL(append + fsync、过滤查询、真实探测记录),由 engine 的证据门(受保护动作前置 probe,SC06 本地半边)与 server 的拒绝记录/查询消费。`runner.json` 目前只有 `evidence_dir`,无策略面。durable profile 宿主已有本地 SQL 库(`DurableConfig.database_url`,SQLite 开发/PostgreSQL 参考)。演进方案 step6 末条要求:容量阈值、留存策略、存储失败显式行为,以及"审计留存可委托已验证业务存储或本地 adapter"。其余子句(意图先于写、fail-closed、待对账、断连本地留存)已交付,不重做。

仓库规则:不新增无第二实现或具名消费者的运行时扩展点——因此委托面随本 change 落两个真实后端,而不是先立抽象。

## Goals / Non-Goals

**Goals:**

- 证据留存策略可配置且缺省行为显式(不设上限、不过期、能力摘要如实报告)。
- 容量/留存的执行语义与现有 fail-closed 门一致:清理后仍超限 → 新受保护动作拒绝,readonly 不受影响。
- 存储失败按类别(不可写/超容量)显式记录与上报。
- durable 宿主可把审计留存委托给本地 SQL 库(同库同引擎),与 JSONL 同契约、同参数化测试。

**Non-Goals:**

- 中心上传/上传重试/上传缓冲(step10;SC06 中心半边,manifest 不翻转)。
- 平台侧 `audit_logs` 或任何平台表变更。
- 审批与授权策略(step7);受管通道不受影响。
- 压缩/归档导出、按 principal 配额(无需求方,不加)。

## Decisions

1. **策略面放 `runner.json` 顶层 `evidence` 块**:`{"dir": ..., "backend": "jsonl"|"durable", "retention_days": int|null, "capacity_limit": int|null}`。统一键名 `retention_days` + `capacity_limit`,计量单位由后端声明(JSONL=字节,SQL=行),校验时按后端解释;不引入按后端分叉的双键。未配置块 = 显式无策略。`evidence_dir` 保留旧位置(向后兼容,块内 `dir` 可覆盖)。
   - 备选:并入 `durable` 块——否,证据留存不属于 durable 契约,且 JSONL 后端也要策略。
   - 备选:JSONL 与 SQL 各一套字段名(字节/行)——否,配置面分叉会让参数化测试与文档双倍,单位声明已足够。
2. **留存清理时机 = 追加后惰性执行 + 门检查前置**:append 后按天文件 mtime/名做删除(便宜、单线程);probe 时先算用量,超限先触发清理,再复检——超限即抛 `EvidenceCapacityError`(OSError 子类,携带类别),engine 门现有 `except OSError` 路径无需改判定,仅丰富 detail。查询接口不触发清理(读路径无副作用)。**实施澄清(apply 期发现)**:清理痕迹(`evidence_gate`/`evidence_retention`)是宿主簿记,不计入容量计量——否则清理痕迹会让复检永远超限、门无法重开;两类后端的 usage 均排除簿记 kind,簿记记录仍随留存期正常过期。
3. **失败类别显式化**:probe/append 失败落一条门拒绝记录尽力而为(同类失败下写门记录可能同样失败——此时以最近一次成功记录 + 内存态最近失败原因为准,健康端点如实报告"最近失败类别与时间",不伪造)。
4. **SQL 后端 = runner 包内新模块,复用 durable 库连接配置,自建表**:`evidence_records`(ts/kind/principal/ref/outcome/detail JSON),`create_schema` 建表;append/query/probe 同 JSONL 契约;容量=行数、留存=按 ts 年龄 DELETE。不进 `hecate-durable`(那三个接缝是任务/命令/台账,证据不是第四接缝);表在宿主自有库,不触 alembic(平台迁移不涉及宿主库)。
5. **一致性钉住**:两后端注册进 `EVIDENCE_IMPLEMENTATIONS` 参数化套件(append/query/过滤/probe/容量拒绝/留存过期),模式照 `DURABLE_IMPLEMENTATIONS`。
6. **能力摘要/健康**:`capabilities_summary` 增证据后端、策略(留存期/容量/缺省)、当前用量与最近门失败;`GET /healthz` 随之携带。

## Risks / Trade-offs

- **容量拒绝是新的失败类别**:与"不可写"同走 fail-closed,但语义是"策略性拒绝"而非故障;以类别字段区分,证据门记录与 API 响应均带类别,避免运维误判磁盘故障。
- **惰性清理的边界**:天文件名解析失败/并发写锁内做删除——删除只针对"当前日期 - retention_days"之前的整文件,不解析内容,失败不阻塞 append(清理失败仅记录,不影响正确性;容量复检仍会拒绝)。
- **SQL 后端行数计量的误差**:并发下超限少量越界可接受(软上限),拒绝发生在门检查点;规格只承诺"清理后仍超限则拒绝",不承诺精确字节级。
- **向后兼容**:无 `evidence` 块的既有 profile 行为不变(除能力摘要多一行报告),回归红线 = 既有 runner 测试全绿。
