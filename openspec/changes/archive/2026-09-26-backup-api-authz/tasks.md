# Tasks: backup-api-authz

## 1. 平台管理员配置与依赖

- [x] 1.1 `core/config.py`：新增 `PLATFORM_ADMIN_API_KEYS: str = ""`、`PLATFORM_ADMIN_EMAILS: str = ""` 配置及 `platform_admin_api_keys_list` / `platform_admin_emails_list` 属性（镜像 `api_keys_list` 风格），补充注释说明用途与 fail-closed 语义。验证：`python -c "from hecate.core.config import settings; assert settings.platform_admin_api_keys_list == []"`。
- [x] 1.2 `core/deps_workspace.py`：`security_scheme` 改为 `HTTPBearer(auto_error=False)`，`get_auth_context` 对缺失凭据显式返回 401（错误体格式沿用现有 `{"error": {...UNAUTHORIZED...}}`）；新增 `ensure_platform_admin(credentials, ctx, db)`（令牌 `hmac.compare_digest` 遍历 + JWT 用户邮箱白名单查询，失败抛 403）与 `require_platform_admin` 依赖（调用前者并返回 `AuthContext`）。验证：`ruff check` + `mypy src/` 通过。
- [x] 1.3 `ops/api/backup.py`：`APIRouter` 挂 `dependencies=[Depends(require_platform_admin)]`，删除 `_require_platform_admin` 及三处调用，端点 docstring 更新。验证：`rg _require_platform_admin src/` 无结果。

## 2. System-scope API key 铸造限制

- [x] 2.1 `enterprise/api/api_keys.py`：`create_api_key` 增加 `credentials` 依赖；`scope=system` 分支先调 `ensure_platform_admin`（403 拒绝），workspace 分支行为不变。验证：`ruff check` 通过。

## 3. 测试

- [x] 3.1 `tests/conftest.py`：新增 `anonymous_client` fixture（仅 override `get_db`，不挂任何 auth override、无 Authorization 头）；提供平台管理员配置的测试辅助（设置/还原 `settings.PLATFORM_ADMIN_API_KEYS`、`PLATFORM_ADMIN_EMAILS`）。验证：现有 `tests/test_api/` 全量仍绿。
- [x] 3.2 重写 `tests/test_api/test_backup_api.py` 为验收矩阵：匿名 → 401（五端点）；workspace admin 无平台凭据 → 403（五端点）且 `create_backup`/`restore_backup`/`verify_backup` mock `assert_not_called`；平台管理员令牌头 → 200；平台管理员邮箱路径（建 `UserModel` 行 + `PLATFORM_ADMIN_EMAILS`）→ 200；`confirm=false` → 400。验证：`pytest tests/test_api/test_backup_api.py -v` 全绿。
- [x] 3.3 `tests/test_api/test_api_key_api.py`：普通用户铸 `scope=system` → 403 且无 key 落库；平台管理员 → 201；workspace 铸造回归不变。验证：`pytest tests/test_api/test_api_key_api.py -v` 全绿。

## 4. 文档

- [x] 4.1 `.env.example`：在 `HECATE_API_KEYS` 附近补 `PLATFORM_ADMIN_API_KEYS` / `PLATFORM_ADMIN_EMAILS`（含一行说明）。验证：文件内容自查。
- [x] 4.2 `docs/reference/rest-api.md`：备份/恢复接口段落补鉴权要求与 401/403 语义。验证：文档自查。

## 5. 整体验证

- [x] 5.1 仓库四项门禁：`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/test_api -q`（全量 `tests/` 由 CI 承担）。验证：全部 0 错误。
- [x] 5.2 `openspec validate --change backup-api-authz` 通过。
