# Tasks

## 1. 数据模型与迁移

- [x] 1.1 `memory_policies` 模型与 migration:workspace 级(workspace_id 唯一)与 agent 级(workspace_id + agent_id 唯一)两类行,结构化列(工具子集、共享上限)+ JSON 参数组;验证:alembic upgrade head 在干净库上通过,唯一约束生效(重复 scope 插入被拒)
- [x] 1.2 `memories` / `knowledge_memories` 增加 `archived_at`(nullable,索引);`last_confirmed_at` 空值行以 `updated_at` 兜底回填;验证:migration up/down 往返通过,回填后空锚点行为零
- [x] 1.3 `memory_edit_log` 操作来源枚举扩展(生命周期来源)与操作原因字段;验证:写入生命周期来源条目的单测通过
- [x] 1.4 平台配置新增 `MEMORY_FLUSH_ENABLED` / `MEMORY_LIFECYCLE_ENABLED`(默认 false)与分层 TTL / 容量 / 预算 / 保护窗口的平台默认值及硬上限;验证:`core/config.py` 单测覆盖默认值与解析,flag 关闭时配置快照与合入前一致

## 2. 策略对象(memory-policy)

- [x] 2.1 策略解析 service:platform → workspace → agent 生效链,空表退化平台默认值,进程内缓存 + 写路径失效;验证:单测覆盖三级解析、空表 no-op、失效后即时生效
- [x] 2.2 收敛规则与校验:权限面字段(工具子集、共享上限)单调收窄校验,数值面字段硬上限钳制校验,未知工具名 / 越权配置 / 超上限数值返回可读错误;验证:单测覆盖每个拒绝分支与"策略不能扩权"场景
- [x] 2.3 策略写入审计(创建/更新/删除记审计日志);验证:单测断言审计条目
- [x] 2.4 工具 seeding 接入策略交集(对应 `agent-memory-tools` delta):可见集合 = 平台 flag 允许集 ∩ 生效策略子集;验证:单测覆盖"策略收窄 memory_forget"、"flag 关闭时策略无效(不扩权)"、无策略行为不变三个场景

## 3. pre_compaction flush(memory-consolidation delta)

- [x] 3.1 compaction surface-replacement 决策点的登记钩子:确定将删窗口后按会话所属整合单元登记 flush 窗口(轻量单行写),`MEMORY_FLUSH_ENABLED` 门控,失败仅告警并放行压缩;验证:单测覆盖登记成功、登记失败压缩照常、flag 关闭零行为三个场景
- [x] 3.2 trigger bus 拾取 flush 窗口:登记窗口复用压力标记优先机制,经整合管线提取(at-least-once + 水位幂等),重试耗尽记指标与日志告警;验证:单测覆盖优先拾取、重复登记水位幂等、重试耗尽告警
- [x] 3.3 可观测性:登记数 / 提取延迟 / 失败计数指标;验证:指标在单测中可断言

## 4. 生命周期(memory-lifecycle)

- [x] 4.1 service 层 archive 过滤单点:`memory_search`、fusion 检索、prefetch、治理 REST 搜索四入口统一排除 `archived_at` 非空行;验证:路径清单测试,四个入口各自断言 archived 不出现、恢复后重现
- [x] 4.2 TTL 过期:清扫按生效策略分层 TTL 检查(`last_confirmed_at` 锚),到期 archive 软删,L4 默认不过期;验证:单测覆盖三层 TTL、L4 豁免、锚点兜底值
- [x] 4.3 容量淘汰:按 scope 计数超限后以 fusion 规范化评分(无查询相关性部分)从低到高 archive,单轮预算限制,保护窗口内不淘汰;验证:单测覆盖超限淘汰顺序、预算截断、保护窗口豁免
- [x] 4.4 跨 namespace 晋升门:评分阈值 + 命中次数 + 存在时长三门槛全满足,经 cross-thread namespace 写路径与隔离校验提升,默认关闭;验证:单测覆盖三门槛、默认关闭、越上限拒绝
- [x] 4.5 清扫调度:trigger bus 新增定期清扫 pass(独立于待处理内容判定),advisory lock 互斥,`MEMORY_LIFECYCLE_ENABLED` 门控;验证:单测覆盖与整合 run 串行、flag 关闭零行为
- [x] 4.6 生命周期审计:TTL 过期 / 淘汰 / 恢复 / 晋升全部记 `memory_edit_log`(来源 + 原因);验证:单测断言每类操作产生审计条目

## 5. 治理 REST API(memory-api delta)

- [x] 5.1 `studio/api` 记忆治理路由组骨架:认证依赖复用、workspace 隔离、editor/admin 角色门控;验证:viewer 角色访问被拒的单测
- [x] 5.2 落地 `memory-api` 既有端点:L1 blocks CRUD、L3 用户记忆查看/语义搜索、压缩状态查询、L4 知识管理(按既有 spec 语义);验证:每端点集成测试(含 workspace 隔离与私有记忆隔离场景)——实现时确认既有端点已存在于 `hecate_memory.api.memory`,本任务收敛为核对其行为与 archive 过滤生效
- [x] 5.3 新增审计查询端点:edit log(按 agent/类型/来源/时间过滤分页)与 consolidation/reflection runs 列表;验证:集成测试覆盖过滤组合与失败原因可见
- [x] 5.4 新增策略 CRUD 端点 + resolved view(标明每字段来源层级);验证:集成测试覆盖创建、越权拒绝、resolved view 来源标注
- [x] 5.5 新增 archive / 恢复 /archived 列表端点与生命周期统计端点(各层计数、archive 原因分布、最近运行摘要);验证:集成测试覆盖 archive→列表→恢复闭环与统计一致性
- [x] 5.6 新增召回对话检索端点(时间窗口、角色过滤、游标、exclude_session_ids);验证:集成测试与 `conversation_search` 工具行为对齐

## 6. Studio 治理 UI(memory-governance-ui)

- [x] 6.1 Memory Center 页面骨架与导航入口(editor+ 可见),三页签布局(L3/L4/召回);验证:构建通过,viewer 角色入口不可见
- [x] 6.2 L3/L4 浏览与语义搜索页(分页、重要度/最后确认时间/来源/scope 展示);验证:组件测试覆盖渲染与搜索交互
- [x] 6.3 archive / 恢复操作与 archived 过滤视图(无内容编辑入口);验证:组件测试覆盖 archive→过滤视图→恢复闭环
- [x] 6.4 审计视图:编辑日志时间线(过滤)与运行列表(状态/失败原因);验证:组件测试覆盖过滤与失败展示
- [x] 6.5 策略编辑表单:workspace 级与 agent 级、校验错误回显、生效链 resolved 预览;验证:组件测试覆盖保存、错误回显、来源层级标注

## 7. 收尾验证

- [x] 7.1 byte-identical 基线验证:两个 flag 关闭 + 空策略表下,全量记忆相关测试与合入前行为一致;验证:`python -m pytest tests/ -q` 全绿(5502 passed;12 个失败逐一定位为分支既有/环境问题,与本 change 无关,其中 1 个路由冲突回归已修复)
- [x] 7.2 spec 场景核对:逐条核对 6 个 delta spec 的 Scenario 与实现/测试映射,补漏;验证:自查清单写入 PR 描述(清单已生成,见 apply 总结;PR 创建时粘贴)
- [x] 7.3 全量校验:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q` 四项全过;验证:CI 本地预跑输出
