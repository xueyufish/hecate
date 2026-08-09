## ADDED Requirements

### Requirement: 两层 Feature Flag 架构（boot-time + runtime）

系统 SHALL 提供两层 feature flag 评估机制：

**Tier 1 (boot-time)**：`src/hecate/core/config.py` 中 `FeatureSettings(BaseSettings)` 类。布尔 flag，影响进程初始化路径（如 `ENABLE_EVENTSTORE_BACKEND`）。通过环境变量配置，改变需重启进程。适用于启动期路径选择。

**Tier 2 (runtime)**：`FeatureFlagModel` ORM + Redis 缓存 + REST API。支持布尔开关、百分比灰度、per-tenant targeting、per-user allowlist。通过 REST API 动态变更，不需重启。适用于运行时功能开关、灰度发布、紧急 kill switch。

两层独立运作：Tier 1 在进程启动时读一次；Tier 2 在每次请求时评估（Redis 缓存 hit < 0.1ms）。

#### Scenario: Tier 1 flag 通过环境变量配置
- **WHEN** 环境变量 `ENABLE_EXPERIMENTAL_RAG=false` 被设置
- **THEN** `settings.feature_settings.ENABLE_EXPERIMENTAL_RAG == False`
- **THEN** 改变此值需重启进程

#### Scenario: Tier 2 flag 通过 REST API 动态变更
- **WHEN** `POST /api/feature-flags/{key}` 请求变更 flag 状态
- **THEN** 变更在 Redis 缓存 TTL 内（默认 5s）传播到所有副本
- **THEN** 不需要重启任何进程

### Requirement: FeatureFlagModel ORM 定义 flag 持久化

`src/hecate/models/feature_flag.py` SHALL 定义 `FeatureFlagModel(Base)` ORM 映射到 `feature_flags` 表：

| 列 | 类型 | 说明 |
|---|---|---|
| `key` | `VARCHAR(128) PK` | 唯一标识（如 `enable_multi_channel`） |
| `status` | `VARCHAR(32) NOT NULL` | lifecycle 状态：`draft` / `active` / `deprecated` / `retired` |
| `enabled` | `BOOLEAN NOT NULL DEFAULT FALSE` | 全局开关 |
| `targeting_rules` | `JSON NULL` | 灰度规则（百分比 / tenant allowlist / user allowlist） |
| `description` | `TEXT NULL` | flag 描述 |
| `created_at` | `TIMESTAMPTZ` | 创建时间 |
| `updated_at` | `TIMESTAMPTZ` | 最后变更时间 |
| `target_removal_version` | `VARCHAR(32) NULL` | 计划移除版本（如 `v0.23`） |
| `evaluation_count` | `BIGINT DEFAULT 0` | 累计评估次数 |
| `last_true_count` | `BIGINT DEFAULT 0` | 累计评估为 true 的次数 |

Alembic migration SHALL 创建该表 + 索引 `idx_feature_flags_status`。

#### Scenario: FeatureFlagModel 可持久化和查询
- **WHEN** 一条 FeatureFlagModel 被创建并 flush
- **THEN** 可通过 `key` 查询到完整记录

#### Scenario: targeting_rules 支持 JSON 格式灰度规则
- **WHEN** `targeting_rules` 被设为 `{"percentage": 15}` 表示 15% 灰度
- **THEN** 评估引擎基于 `(tenant_id or user_id) hash % 100 < 15` 判定是否启用

### Requirement: Feature flag lifecycle 状态机

每个 Tier 2 feature flag SHALL 遵循生命周期状态机：

```
draft → active → deprecated → retired → (deleted)
```

- `draft`：已创建但未生效。评估结果恒为 `False`。
- `active`：正常工作。评估引擎根据 `enabled` + `targeting_rules` 返回结果。
- `deprecated`：已被标记为废弃。评估仍工作（保持向后兼容），但 `hecate flag-audit` 输出 WARNING。必须在 `target_removal_version` 前升级到 `retired`。
- `retired`：已退役。评估结果恒为 `False`。代码中的引用应已移除。CI `hecate flag-audit --check` 对 retired flag 有代码引用时 FAIL。

状态转换 SHALL 通过 REST API `POST /api/feature-flags/{key}/transition` 触发，记录审计日志。

#### Scenario: draft flag 评估恒为 False
- **WHEN** flag status 为 `draft`
- **THEN** `evaluate("flag_key", tenant_id, user_id)` 恒返回 `False`

#### Scenario: active flag 按 targeting_rules 评估
- **WHEN** flag status 为 `active`、`enabled=True`、`targeting_rules={"percentage": 50}`
- **THEN** 约 50% 的请求评估为 `True`（基于 consistent hash）

#### Scenario: deprecated flag 仍工作但审计工具告警
- **WHEN** flag status 为 `deprecated`
- **THEN** 评估正常工作（与 active 相同）
- **THEN** `hecate flag-audit` 输出 WARNING "flag X is deprecated, target removal: v0.23"

### Requirement: Feature flag 评估引擎

`src/hecate/services/feature_flags/evaluator.py` SHALL 提供 `async def evaluate(key, *, tenant_id=None, user_id=None) -> bool` 方法。

评估逻辑：
1. 读 Redis 缓存 `feature_flag:{key}` → miss 则读 DB → 写 Redis（TTL 5s）
2. flag status != `active` → 返回 `False`（draft/retired）或 `enabled`（deprecated 与 active 行为一致但审计告警）
3. `enabled == False` → 返回 `False`
4. `targeting_rules` 为空 → 返回 `True`（全局开启）
5. `targeting_rules` 含 `tenant_allowlist` → `tenant_id in allowlist` 则 `True`
6. `targeting_rules` 含 `user_allowlist` → `user_id in allowlist` 则 `True`
7. `targeting_rules` 含 `percentage` → `hash(tenant_id or user_id or session_id) % 100 < percentage` 则 `True`
8. 其他情况 → `False`

评估 SHALL 使用 consistent hash（如 `mmh3` 或 `hashlib.sha256`），确保同一用户在 flag 未变更时总是得到相同结果。

每次评估 SHALL 递增 `evaluation_count`（异步批量写入，不阻塞请求路径）+ 如果结果为 `True` 递增 `last_true_count`。

每次评估 SHALL 创建 OTel span（`feature_flag.evaluate`）含 attribute `flag.key` / `flag.result` / `flag.targeting`。

#### Scenario: 缓存 miss 时读 DB 并回填
- **WHEN** Redis 中无 `feature_flag:{key}` 缓存
- **THEN** 从 DB 读取 FeatureFlagModel → 写入 Redis（TTL 5s）→ 返回评估结果

#### Scenario: percentage 灰度基于 consistent hash
- **WHEN** `targeting_rules={"percentage": 30}` 且同一 `user_id` 连续评估 100 次
- **THEN** 约 30 次返回 `True`，70 次返回 `False`
- **THEN** 同一 `user_id` 的结果在 flag 未变更时一致

#### Scenario: OTel span 记录评估
- **WHEN** 评估完成
- **THEN** OTel span `feature_flag.evaluate` 含 `flag.key` / `flag.result` / `flag.targeting` 属性

### Requirement: Feature flag REST API

`src/hecate/api/management/feature_flags.py` SHALL 提供：

- `GET /api/feature-flags`：列出所有 flag（含 status / enabled / evaluation_count / last_true_ratio）
- `GET /api/feature-flags/{key}`：查询单个 flag 详情
- `POST /api/feature-flags`：创建新 flag（status=draft）
- `PATCH /api/feature-flags/{key}`：更新 flag（enabled / targeting_rules / description）
- `POST /api/feature-flags/{key}/transition`：状态转换（draft→active / active→deprecated / deprecated→retired）
- `DELETE /api/feature-flags/{key}`：删除 retired flag

所有变更 SHALL 记录审计日志（who / what / when / old_value / new_value）。

所有变更 SHALL 在写入 DB 后立即 invalidate Redis 缓存 `DEL feature_flag:{key}`。

#### Scenario: 创建新 flag
- **WHEN** `POST /api/feature-flags` body 为 `{"key": "enable_dlp", "description": "DLP engine"}`
- **THEN** 创建 FeatureFlagModel status=draft enabled=False
- **THEN** 返回 HTTP 201

#### Scenario: 变更后 invalidate Redis 缓存
- **WHEN** `PATCH /api/feature-flags/{key}` 更新 enabled=True
- **THEN** DB 更新后立即 `DEL feature_flag:{key}` 使缓存失效
- **THEN** 下一次评估从 DB 重新加载

### Requirement: hecate flag-audit CLI 审计工具

`src/hecate/cli/flag_audit.py` SHALL 提供 CLI 命令 `hecate flag-audit`。

**默认模式**：扫描 `src/hecate/` 目录下所有 `.py` 文件，用 Python 标准库 `ast` 解析，找出所有 `settings.ENABLE_*` 或 `settings.feature_settings.ENABLE_*` 引用。输出表格：

```
Flag                           Status       Eval/Day  True%  References           Action
──────────────────────────────────────────────────────────────────────────────────────────
ENABLE_NEW_AGENT_ENGINE       active       12,341    100%   3 files (engine/)     → deprecate
ENABLE_MULTI_CHANNEL          active        8,721     15%   7 files (api/ch/)     → keep
ENABLE_LEGACY_AUTH            deprecated      142    100%   1 file (auth.py)      → remove NOW
ENABLE_EXPERIMENTAL_RAG       draft             0       —    0 files               → no refs
```

**`--check` 模式**（CI 集成）：
- `status == deprecated` 且当前版本 >= `target_removal_version` → **FAIL**
- `status == retired` 且代码中仍有引用 → **FAIL**
- `status == draft` 且 `evaluation_count == 0` 且创建超过 30 天 → WARNING

#### Scenario: 审计工具检测到过期 flag
- **WHEN** flag `ENABLE_LEGACY_AUTH` status=deprecated，target_removal_version=v0.22，当前版本 v0.23
- **THEN** `hecate flag-audit --check` 退出码 1（FAIL）
- **THEN** 输出 "ENABLE_LEGACY_AUTH: deprecated flag past removal target v0.22 (current: v0.23)"

#### Scenario: 审计工具检测到 retired flag 仍有引用
- **WHEN** flag `ENABLE_OLD_UI` status=retired 但 `src/hecate/api/ui.py` 仍有 `settings.feature_settings.ENABLE_OLD_UI`
- **THEN** `hecate flag-audit --check` 退出码 1（FAIL）
- **THEN** 输出 "ENABLE_OLD_UI: retired flag still referenced in src/hecate/api/ui.py"

#### Scenario: 审计工具检测到零引用 flag
- **WHEN** flag `ENABLE_EXPERIMENTAL_RAG` 在 `src/hecate/` 中无任何代码引用
- **THEN** 审计表格 References 列显示 "0 files"
- **THEN** Action 列显示 "→ no refs"
