# Tasks

任务组 1—2 对应 PR1(基线文档),任务组 3—4 对应 PR2(manifest/fixture);组 5 收尾。每组完成即勾选,不以组间顺序阻塞先行验证。

## 1. 实施快照与依赖闭包(基线文档前半)

- [x] 1.1 新建 `docs/research/standalone-consumption-baseline.md`,写基线声明表(分支、提交、在途 change、包版本、核验方法),并链接 `platform-evolution-baseline.md` 声明快照不覆盖;验证:`git log --oneline -1`、`openspec list --json`、`uv pip list` 与表格一致
- [x] 1.2 梳理执行链依赖闭包表:启动配置 → 定义加载 → 图编译 → Worker/策略 → 身份/策略 → 模型/工具 → checkpoint → 证据,逐行登记代码位置(file:line)、是否执行必需、分类(宿主可注入/可选安装/待解除耦合)、责任 step;`runtime/AGENTS.md` 懒加载清单逐行归类引用;验证:表内每个代码位置可在工作副本定位,未核验项带【未核验】标记
- [x] 1.3 静态核对 wheel 依赖:读取根 `pyproject.toml` `[project].dependencies` 与 `packages/*/pyproject.toml`,与闭包表交叉标注(哪些"执行必需"项当前在主应用依赖内、哪些可选包未列),并声明"import 探针通过 ≠ 可独立安装";验证:闭包表分类与依赖清单逐项对得上

## 2. 模式拓扑与验收规格/差距表(基线文档后半)

- [x] 2.1 写独立/受管/完整私有平台三节拓扑:网络模式、可信身份来源、任务/执行状态 owner、凭据与数据出口;受管断连行显式登记授权有效期、最大陈旧窗口与重连责任方,标注为目标语义(待 step6/7 落地);验证:与方案 §一"状态与信任规则"逐项对应,B1—B5 以链接引用原基线 §4 不复制
- [x] 2.2 写验收规格登记表,固定格式 `SC ID | 能力 | 当前状态 | 责任 step`,覆盖 SC01—SC10;当前状态枚举仅 `未支持`/`未验证`/`未支持/未验证`;验证:任务 3.2 的一致性断言(SC id 覆盖 + planned 状态对齐)通过
- [x] 2.3 写差距表:step5(安装与只读执行)、step6/7(持久化/本地强制策略/受管断连)、step10/11(证据与制品门禁)、step16(组合认证)各差距项挂"实现 owner:待指派 / 验收 owner:待指派 / 指派时点:—"字段;验证:方案 step1 增量第 6 项列出的每个责任 step 都有对应行,无虚构人员
- [x] 2.4 登记条件性延期项(托管执行组合数据流 → step4 Deployment 模型;真实成本基线 → G4 关闭),说明本 change 不强行完成;验证:表述与 manifest SC 组及原基线 §5/§9 口径一致

## 3. SC 场景组与一致性测试

- [x] 3.1 在 `tests/scenarios/manifest.yaml` 追加顶层键 `sc_scenarios`(schema_version 保持 1),SC01—SC10 条目 title 逐字对齐方案 §八行名,字段 `id/title/run_mode/status/tier/assertions/responsible_step/blocked_by`,全部 `planned`,`blocked_by` 用方案 §七建议 change 名并在注释声明其为建议;验证:现有 `test_manifest_consistency.py` 原有四项测试仍通过(S/P 语义未变)
- [x] 3.2 扩展 `tests/scenarios/test_manifest_consistency.py`:SC 结构校验(`SC\d+` 唯一、run_mode/status/tier 枚举、`planned` 必须有 `responsible_step`+`blocked_by`)、未来 `implemented`↔`test_sc<nn>_*` 双向绑定守卫(当前应报"无 SC 测试"而非静默)、SC id ⊆ 基线文档登记表、manifest `planned` 时对应登记行不得含"已支持/已通过"标记;验证:pytest 通过,且临时构造违规 manifest(删 blocked_by、登记行写已支持)时断言失败后还原
- [x] 3.3 回归确认 S/P 条目零改动;验证:`git diff tests/scenarios/manifest.yaml` 仅含新增 `sc_scenarios` 键,`pytest tests/scenarios/ -q` 全绿

## 4. 库存 fixture 与 fixture 级测试

- [x] 4.1 实现 `tests/scenarios/tools/inventory_api.py`:`StubInventoryApi` 内存服务,两个隔离数据域(合成库存记录)、只读身份(绑定单域)、审批门禁写操作,每次调用记录 `(operation, domain, identity, outcome)`,拒绝 outcome(`denied_cross_domain`/`denied_read_only`/`denied_unapproved`)可查询;验证:模块仅依赖 stdlib/dataclass,无平台源码 import
- [x] 4.2 编写 `tests/scenarios/test_sc_fixture_inventory.py`(函数前缀 `test_sc_fixture_`):域隔离拒绝、只读身份越域/写拒绝、未审批写零状态变更、获批后恰一次、重复调用不产生第二次变更;验证:pytest 全绿,docstring 声明"fixture 正确性验证,不声称独立宿主能力"

## 5. 验证与收尾

- [x] 5.1 本地四项验证:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`(无 src 改动,应零差异)、`python -m pytest tests/scenarios/ -q`;验证:全部 0 错误
- [x] 5.2 `openspec validate standalone-consumption-baseline --strict` 通过;验证:命令输出无 error
- [x] 5.3 更新方案文档 step1"独立消费增量"6 个未勾选项为已勾选,各附证据指针(新基线文档章节、manifest `sc_scenarios`、fixture 路径);验证:`git diff` 仅涉及这 6 行及其括注
