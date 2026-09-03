"""Enterprise domain — identity and governance half of the main package.

Companion to ``packages/hecate-enterprise`` (the SSO/SCIM/cloud-vault/
budget/tenant workspace package). This main-package directory holds
the abstract interfaces and always-on base implementations; the
commercial / advanced implementations live in the workspace package
and register via entry-points:

- ``hecate.auth_providers`` — SSO providers (OIDC / SAML / LDAP)
- ``hecate.secret_providers`` — cloud secret backends (AWS / Azure /
  HashiCorp Vault)

Two-half split rationale
------------------------

``core/deps_workspace.py`` depends on the auth resolver structurally
(it runs the auth chain on every request), so the base auth mechanism
stays in core. SSO/cloud-vault/SCIM/tenant are commercial gating
candidates — they live in the workspace package so that
``pip install hecate-enterprise`` / paid-bundle / license gating all
land without modifying core.

History: founded as a domain directory during Phase R-complete
(``refactor(domain): move auth/vault + services/auth/password,token
to enterprise/``).
"""

from __future__ import annotations
