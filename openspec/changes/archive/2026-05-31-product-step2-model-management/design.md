## Context

Hecate 当前的模型管理存在两个核心问题：

1. **模型列表不可控** — `/v1/models` 直接调用 LiteLLM `get_valid_models()` 返回 200+ 模型，包含 image/audio/embedding 等无关类型，用户体验差
2. **Provider 配置不灵活** — API Key 硬编码在 `.env`，重启才能生效，无法多人使用

Step 1 已完成基础产品循环（注册→登录→创建 Agent→对话），Step 2 需要让平台可被多人使用。

当前架构：
- `src/hecate/api/v1/models.py` — 调用 `_discover_models()` 返回 LiteLLM 模型列表
- `src/hecate/core/config.py` — `HECATE_API_KEYS` 环境变量
- `src/hecate/services/llm/service.py` — LiteLLM wrapper，`_resolve_model()` 解析模型名
- `web/src/app/(dashboard)/agents/new/page.tsx` — 模型下拉选择

## Goals / Non-Goals

**Goals:**
- 数据库存储 Provider 和模型，替代纯 env 配置
- 后台 UI 管理 Provider（CRUD + 测试连通）
- 按 Provider 分组展示模型选择
- API Key Fernet 加密存储
- 模型调试面板（选模型、输 prompt、调参数、看响应）
- Provider 级 timeout/retry/rate limit 配置
- Provider 状态变化时更新 Agent fallback 链

**Non-Goals:**
- 多租户/权限隔离（P3）
- 模型路由/成本追踪（P3）
- 自托管推理（P4）
- 模型精调（P4）

## Decisions

### D1: 数据库存储 vs 环境变量

**选择**: 数据库存储 Provider 和模型，`.env` 作为 fallback

**理由**:
- 数据库存储支持动态增删改，不需要重启
- `.env` fallback 保证开发环境零配置
- LiteLLM `get_valid_models()` 仅在添加 Provider 时调用，不做实时查询

**替代方案**:
- 纯 env 配置：不支持动态管理，排除
- Redis 缓存：增加运维复杂度，P2 阶段不需要

### D2: API Key 加密方案

**选择**: Fernet 对称加密（`cryptography` 库）

**理由**:
- Fernet 是 Python 标准加密方案，简单可靠
- 密钥从 `FERNET_KEY` env 读取，未设置时明文存储兼容开发
- 生产环境一个 env var 即可启用加密

**替代方案**:
- AES-GCM：更底层，需要自己处理 padding/nonce，复杂度高
- RSA：非对称加密，Key 管理复杂，这里不需要
- Hashicorp Vault：外部依赖重，P2 不需要

### D3: Provider 配置存储方式

**选择**: `config JSON` 字段存 timeout/retry/rate_limit

**理由**:
- 灵活扩展，不需要改表结构
- 默认值在应用层处理，数据库只存非默认值
- JSON 字段在 SQLAlchemy 中已有成熟支持

### D4: 模型调试实现

**选择**: 新增 `POST /api/models/test` 端点，复用 `llm_service.chat()`

**理由**:
- 复用现有 LLM 调用链路，不引入新依赖
- 前端简单表单：选模型 → 输 prompt → 调参数 → 看响应
- 响应直接返回给前端，不做流式（调试场景不需要）

### D5: Fallback 集成方式

**选择**: Provider 状态变化时，检查关联 Agent 并记录警告

**理由**:
- 不自动修改 Agent 配置（避免意外行为）
- 在 Agent 列表页显示"模型不可用"警告
- 用户手动切换模型或添加新 Provider

### D6: 前端技术栈

**选择**: 继续使用 Next.js + shadcn/ui + Tailwind

**理由**:
- Step 1 已建立，保持一致
- shadcn/ui 的 Table、Dialog、Select 组件满足需求
- 分组下拉用 Radix UI 的 `OptGroup` 模式

## Risks / Trade-offs

**[R1] Fernet 加密密钥丢失** → 密钥丢失无法解密 API Key。缓解：生产环境密钥管理交给运维，文档说明密钥备份重要性。

**[R2] LiteLLM 模型发现依赖网络** → 添加 Provider 时如果 LiteLLM API 不通会失败。缓解：异步发现 + 手动添加兜底。

**[R3] config JSON 字段无 schema 校验** → 可能存入非法配置。缓解：应用层校验 timeout/range/rate_limit 合法性。

**[R4] Agent fallback 链未实现** → P1 的 `llm_service` 没有真正的 fallback 机制（已移除）。缓解：Step 2 只做警告，真正的 fallback 在 P3 实现。
