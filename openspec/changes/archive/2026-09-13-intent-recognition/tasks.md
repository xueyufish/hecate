# Tasks: intent-recognition

> 分期对应 design.md D9:第 1-3 组 = 引擎 + 意图包;第 4-5 组 = 消费者;第 6-7 组 = 画布 + 评测联动。

## 1. 数据模型与迁移

- [x] 1.1 新建 `models/intent_package.py`:`IntentPackageModel`(workspace 隔离、name 唯一、软删)、`IntentPackageCategoryModel`(name/description/domain/policy_gated)、`IntentPackageSampleModel`(utterance + provenance JSON)、`IntentPackageVersionModel`(name per package 唯一、冻结内容 JSON、content_hash、published_at、gate_report、软删)
- [x] 1.2 加性 Alembic 迁移(三/四张新表,存量零改动),升级+降级验证
- [x] 1.3 版本冻结复用 dataset 快照的 canonical 序列化与 content_hash 实现抽取为共享工具,单测对齐两种资产产出的 hash 语义

## 2. 意图包资产服务(studio)

- [x] 2.1 CRUD service:创建/列表/读取/更新/删除,workspace 隔离与越权拒绝,name 冲突 409
- [x] 2.2 CSV/JSON 批量导入导出:全或无导入、行级错误报告、merge/replace 模式、导出 round-trip
- [x] 2.3 版本生命周期:list/get/软删(名称保留、既有引用可读)
- [x] 2.4 发布门禁:`warn`/`require` 模式,确定性信号(per-category coverage 下限 + 关联 run 确定性 pass_rate);`require` 未满足返回 409 + 完整 gate report;`force` 绕过 + `bypassed_by_force` 标记 + 审计;LLM judge/human 分数不参与阻断
- [x] 2.5 纠错回流端点:追加 draft 样例/分类修正,provenance(source session/turn、reason、submitter)必填,不触碰已发布版本
- [x] 2.6 API 路由(包、分类、样例、版本、发布、纠错)+ `tests/test_api/` 契约测试
- [x] 2.7 service 单测(`tests/test_services/test_intent_packages/`):门禁判定矩阵、导入错误报告、版本不可变

## 3. runtime 识别引擎

- [x] 3.1 `runtime/intent/types.py`:`IntentRecognitionResult`(L1 atomic label/confidence/source、L2 workflow、L3 session、L4 domain?、gated 标记)、`EvidencePayload`、`IntentEvidencePort` ABC(cross-layer Port 命名)
- [x] 3.2 L1 管线:有序快路径(缓存 → 包 patterns → few-shot → LLM);few-shot 有界采样(默认 5/类、总 30)与确定性轮换;LLM 结构化输出按包分类枚举校验,失败落 fallback
- [x] 3.3 `IntentDecisionCache`:进程内 LRU,键 =(normalized utterance sha256, package_version_id, context_fingerprint),TTL/容量可配,hit 标记外露
- [x] 3.4 L2 工作流意图:会话滑动窗口信号(原子意图关联性 + 多步线索)+ 阈值激活,模糊时单次 LLM 判定
- [x] 3.5 L3 会话意图:SessionState 新增字段 + 漂移检测合并策略(显式漂移替换、否则沿用)+ 会话恢复直读
- [x] 3.6 L4 domain 按命中分类带出;L5 `policy_gated` 结果标注(执行侧由 deterministic hooks 强制,识别输出不可越过)
- [x] 3.7 组合根接线:studio 侧 evidence provider 实现 `IntentEvidencePort`(只读已发布版本快照);`StubIntentEvidencePort` 测试替身;自足性确认(runtime 无新懒导入桥)
- [x] 3.8 加性 `EventType.INTENT_RECOGNIZED`:层级结果、决策来源、cache hit、证据引用(package_id+version)、延迟;不含 few-shot payload;LogPolicy 不排除
- [x] 3.9 识别模型独立配置:经 `llm_invoke(model=...)` 解析,默认平台默认模型
- [x] 3.10 引擎单测(`tests/test_runtime/test_intent/`):管线顺序、缓存命中/失效、枚举校验 fallback、漂移合并、降级路径(证据读取失败)

## 4. 控制器节点(2.6a)

- [x] 4.1 `NodeType.CONTROLLER` + DSL schema + runtime 形状校验(default workflow 必填、category_targets 非空、route target 已声明)
- [x] 4.2 `runtime/workers/controller_worker.py`:逐轮调引擎、写 `_route`(mapped category 目标 + `default`/`start`/`end` 命名边)、全局意图持有与漂移重路由
- [x] 4.3 子工作流派发复用 agent-node 子图路径:child session、显式 channel mapping、WorkerResult 失败契约;未注册父通道编译期报错
- [x] 4.4 加性 `EventType.CONTROLLER_ROUTED`:目标、触发层级/label、confidence、source、cache hit、会话意图状态
- [x] 4.5 控制器测试:路由矩阵(命中/工作流意图/default)、黏滞路由与漂移重路由、隔离语义、事件断言

## 5. INTENT 路由模式委托(2.7c 升级)

- [x] 5.1 `routing_config` 扩展 `intent_package`(id + 可选 version pin)+ `category_targets`;studio 图保存路径做包引用与分类 key 存在性校验(运行时编译器只做形状校验)
- [x] 5.2 `runtime/routing.py::_evaluate_intent` 包分支:委托引擎、`_route` 取映射目标、无匹配落 default、证据失败降级为 category_targets 上的 LLM 分类;legacy(`intent_patterns`/`routing_prompt`)行为逐字节保留
- [x] 5.3 回归 + 委托测试:legacy 不变性、委托命中/缓存、校验拒绝(未知包/未知分类/两者皆无)

## 6. 控制器画布(1.1.21)

- [x] 6.1 CONTROLLER 节点渲染 + 配置面板:意图包选择器(published versions + 版本 pin,默认 latest published)
- [x] 6.2 映射编辑器:行 = 所选版本的分类,route target 限于图中已声明节点;版本切换时保留仍存在的映射、移除分类标红要求重指派;start/default/end 选择器 + 全局意图面板(enable + goal hint)
- [x] 6.3 映射可视化:分类映射边与 default 边视觉区分
- [x] 6.4 校验错误逐字段呈现、非法配置不落盘;graph DSL 持久化;web `__tests__`

## 7. 评测联动(复用 7.x)

- [x] 7.1 版本 → held-out 评测数据集生成(split 可配,样例转 item)
- [x] 7.2 识别准确率 run 触发(7.2c 任务机)+ per-category accuracy 结果回读与版本关联
- [x] 7.3 门禁 accuracy 信号消费关联 run 结果,gate report 引用 run id

## 8. 收尾

- [x] 8.1 自足性 probe / 层级扫描核对(如无新桥接项,确认 allowlist 不变;有则登记并更新 `src/hecate/runtime/AGENTS.md`)
- [x] 8.2 文档:`docs/gotchas.md` 补引擎/缓存/版本语义坑;`docs/design/engine-design.md` 的 5-Level Intent Recognition 段从 Planned 改为 shipped 形态(含 L5=策略门的裁决)
- [x] 8.3 全量验证:`ruff check` + `ruff format --check` + `mypy` + `pytest tests/ -q` 全绿
- [x] 8.4 准备归档清单:feature-catalog 6.23⊕1.3.10/6.49/2.6a/1.1.21 状态、roadmap Opening Queue 勾选、positioning.md 特性描述(归档时执行)
