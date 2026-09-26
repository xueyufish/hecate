-- Audit existing database-issued SYSTEM-scope API keys.
--
-- Since auth-boundary-hardening, these keys are rejected at
-- authentication time (their minting endpoint was unrestricted before
-- the platform-admin gate, so provenance is untrustworthy). Use this
-- query to inventory affected integrations, then migrate them to:
--   * env bootstrap keys (HECATE_API_KEYS / PLATFORM_ADMIN_API_KEYS)
--     for system-level service access, or
--   * workspace-scoped API keys (POST /api/api-keys, scope=workspace)
--     for tenant-scoped access.
--
-- Run against the primary PostgreSQL database (psql / migration tooling).

SELECT
    k.id,
    k.name,
    k.key_prefix,
    k.created_by,
    u.email            AS created_by_email,
    k.created_at,
    k.last_used_at,
    k.is_active
FROM api_keys AS k
LEFT JOIN users AS u ON u.id = k.created_by
WHERE k.scope = 'system'
  AND k.deleted = false
ORDER BY k.created_at;

-- Optional cleanup after integrations have been migrated (irreversible —
-- prefer the soft-delete path used by the API, or deactivate first):
-- UPDATE api_keys SET is_active = false WHERE scope = 'system' AND deleted = false;
