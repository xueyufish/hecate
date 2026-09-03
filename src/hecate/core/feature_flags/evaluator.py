"""Feature flag evaluator — applies targeting rules to produce bool result.

State machine:
  draft      → always returns False
  active     → applies enabled + targeting_rules
  deprecated → same as active (flag audit tool warns)
  retired    → always returns False

Targeting rules format (JSON in DB):
  {"percentage": 30}                              # hash of (flag_key + user_id or tenant_id)
  {"tenant_allowlist": ["uuid1", "uuid2"]}
  {"user_allowlist": ["uuid1", ...]}
  Multiple keys are AND-ed.
"""

from __future__ import annotations

import hashlib

FLAG_ACTIVE = "active"
FLAG_DEPRECATED = "deprecated"
FLAG_DRAFT = "draft"
FLAG_RETIRED = "retired"

VALID_STATUSES = {FLAG_DRAFT, FLAG_ACTIVE, FLAG_DEPRECATED, FLAG_RETIRED}


def _stable_hash(flag_key: str, tenant_id: str | None, user_id: str | None) -> int:
    seed = tenant_id or user_id or "anonymous"
    digest = hashlib.sha256(f"{flag_key}:{seed}".encode()).hexdigest()
    return int(digest[:8], 16)


class FeatureFlagEvaluator:
    """Stateless rule evaluator — caller loads flag dict from cache or DB."""

    def evaluate(
        self,
        flag_dict: dict | None,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> bool:
        """Return True if the flag is active and the targeting rules pass."""
        if not flag_dict:
            return False

        status = flag_dict.get("status")
        if status in (FLAG_DRAFT, FLAG_RETIRED):
            return False
        if status not in VALID_STATUSES:
            return False

        if not flag_dict.get("enabled", False):
            return False

        rules = flag_dict.get("targeting_rules") or {}
        return self._match_rules(rules, flag_key=flag_dict.get("key", ""), tenant_id=tenant_id, user_id=user_id)

    def _match_rules(
        self,
        rules: dict,
        *,
        flag_key: str,
        tenant_id: str | None,
        user_id: str | None,
    ) -> bool:
        if not rules:
            return True

        tenant_allowlist = rules.get("tenant_allowlist")
        if tenant_allowlist and (not tenant_id or tenant_id not in tenant_allowlist):
            return False

        user_allowlist = rules.get("user_allowlist")
        if user_allowlist and (not user_id or user_id not in user_allowlist):
            return False

        percentage = rules.get("percentage")
        if percentage is not None:
            h = _stable_hash(flag_key, tenant_id, user_id)
            if h % 100 >= percentage:
                return False

        return True
