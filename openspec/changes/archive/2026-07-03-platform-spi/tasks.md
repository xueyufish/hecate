## 1. 通道适配器核心（channel-adapter 规范）

- [x] 1.1 创建 `src/hecate/channel/__init__.py`，包含公共导出（ChannelABC、CanonicalMessage、ChannelCapabilities）
- [x] 1.2 创建 `src/hecate/channel/types.py`，包含 CanonicalMessage 冻结数据类（id、channel_id、user_id、session_id、content、metadata、timestamp）和 MessageContent 数据类（text、attachments）
- [x] 1.3 创建 `src/hecate/channel/capabilities.py`，包含 ChannelCapabilities 冻结数据类（streaming、interactive_buttons、file_upload、markdown、rich_cards、max_message_length）
- [x] 1.4 创建 `src/hecate/channel/adapter.py`，包含 ChannelABC 抽象基类（name、description、capabilities 属性；receive、respond、stream 抽象方法）

## 2. Gateway 层（channel-adapter 规范）

- [x] 2.1 创建 `src/hecate/gateway/__init__.py`，包含公共导出（Gateway）
- [x] 2.2 创建 `src/hecate/gateway/session.py`，包含 SessionRouter（session_id → channel_id/user_id 映射、创建/恢复逻辑）
- [x] 2.3 创建 `src/hecate/gateway/gateway.py`，包含 Gateway 类（从通道接受 CanonicalMessage、通过 SessionRouter 解析会话、委托给 WorkflowExecutionService）

## 3. 通道插件注册（channel-adapter 规范）

- [x] 3.1 更新 `src/hecate/plugin/spi/__init__.py` 以导出 ChannelABC
- [x] 3.2 在 `src/hecate/gateway/registration.py` 中创建 `register_channels(registry)` 函数，用于注册内置通道（RESTChannelAdapter 占位符）

## 4. NotificationDispatcher 重构（channel-adapter 规范）

- [x] 4.1 创建 `src/hecate/channel/notification.py`，包含 NotificationChannelAdapter 基类，将现有的渲染函数包装为 Channel respond() 实现
- [x] 4.2 重构 `src/hecate/services/notification_dispatcher.py` 以使用 NotificationChannelAdapter 替代 switch/case 分发

## 5. AuthProviderABC（auth-provider 规范）

- [x] 5.1 创建 `src/hecate/auth/__init__.py`，包含公共导出（AuthProviderABC、JWTAuthProvider、APIKeyAuthProvider）
- [x] 5.2 创建 `src/hecate/auth/provider.py`，包含 AuthProviderABC 抽象基类（name、description 属性；authenticate 抽象方法）
- [x] 5.3 创建 `src/hecate/auth/jwt_provider.py`，包含 JWTAuthProvider，包装现有的 `decode_access_token()` 并返回 AuthContext
- [x] 5.4 创建 `src/hecate/auth/api_key_provider.py`，包含 APIKeyAuthProvider，包装现有的 `_resolve_api_key()` 逻辑并返回 AuthContext

## 6. 认证提供者集成（auth-provider 规范）

- [x] 6.1 创建 `src/hecate/auth/resolver.py`，包含 `resolve_auth_context(credentials, db)` 函数，遍历已注册的认证提供者
- [x] 6.2 更新 `src/hecate/core/deps_workspace.py`，使 `get_auth_context()` 委托给 `resolve_auth_context()`，同时保留现有行为
- [x] 6.3 创建 `register_auth_providers(registry)` 函数，注册 JWTAuthProvider 和 APIKeyAuthProvider

## 7. i18n 核心（i18n-spi 规范）

- [x] 7.1 创建 `src/hecate/i18n/__init__.py`，包含公共导出（LocaleResolver、MessageCatalog、t）
- [x] 7.2 创建 `src/hecate/i18n/locale_resolver.py`，包含 LocaleResolver（优先级：显式参数 → Accept-Language 头 → 用户偏好 → 工作区默认 → 系统默认 "en"）
- [x] 7.3 创建 `src/hecate/i18n/catalog.py`，包含 MessageCatalog，从 `locales/{locale}/{namespace}.json` 或 `.yaml` 加载，支持嵌套键查找和参数插值
- [x] 7.4 创建 `src/hecate/i18n/translate.py`，包含 `t(key, locale=None, **params)` 函数，使用 LocaleResolver 和 MessageCatalog

## 8. i18n 数据模型（i18n-spi 规范）

- [x] 8.1 向 `src/hecate/models/user.py` 的 UserModel 添加 `preferred_locale`（可选 str）字段
- [x] 8.2 向 `src/hecate/models/workspace.py` 的 WorkspaceModel（如果存在）添加 `default_locale`（可选 str，默认 "en"）字段
- [x] 8.3 为新语言字段创建 Alembic 迁移

## 9. i18n 插件翻译注册（i18n-spi 规范）

- [x] 9.1 更新 `src/hecate/plugin/manifest.py`，向 PluginManifest 添加 `translations: tuple[str, ...] = ()` 字段
- [x] 9.2 更新 `src/hecate/plugin/registry.py`，在注册时自动加载插件翻译

## 10. i18n 管理 API（i18n-spi 规范）

- [x] 10.1 创建 `src/hecate/api/management/i18n.py`，包含 REST 端点：POST /api/i18n/translations、GET /api/i18n/translations/{locale}、GET /api/i18n/locales、PUT /api/i18n/translations/{locale}/{namespace}
- [x] 10.2 在 `src/hecate/main.py` 中注册 i18n 路由器

## 11. Plugin SPI __init__.py 更新

- [x] 11.1 更新 `src/hecate/plugin/spi/__init__.py` 以导出所有新的 ABC（ChannelABC、AuthProviderABC）

## 12. 测试

- [x] 12.1 创建 `tests/test_channel/test_adapter.py` — 测试 ChannelABC 接口、CanonicalMessage 不可变性、ChannelCapabilities 默认值
- [x] 12.2 创建 `tests/test_channel/test_gateway.py` — 测试 Gateway 会话路由、消息标准化
- [x] 12.3 创建 `tests/test_auth/test_provider.py` — 测试 AuthProviderABC 接口、JWTAuthProvider、APIKeyAuthProvider
- [x] 12.4 创建 `tests/test_auth/test_resolver.py` — 测试提供者迭代、回退行为
- [x] 12.5 创建 `tests/test_i18n/test_locale_resolver.py` — 测试语言检测优先级链
- [x] 12.6 创建 `tests/test_i18n/test_catalog.py` — 测试 JSON/YAML 加载、嵌套键、参数插值、回退
- [x] 12.7 创建 `tests/test_i18n/test_translate.py` — 测试 t() 函数端到端
- [x] 12.8 运行完整测试套件：`ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
