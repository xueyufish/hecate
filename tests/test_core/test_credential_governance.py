"""Credential governance: plaintext rejection and identity headers (task 4.3)."""

from __future__ import annotations

from hecate_governance.secrets import find_plaintext_secrets


class TestPlaintextSecretRejection:
    def test_plaintext_api_key_flagged(self) -> None:
        findings = find_plaintext_secrets({"api_key": "sk-live-123456"})
        assert findings == ["api_key"]

    def test_reference_values_accepted(self) -> None:
        findings = find_plaintext_secrets(
            {
                "api_key": "env:BUSINESS_API_KEY",
                "secret": "file:/etc/hecate/secret",
                "token": "ref:credentials/inventory",
                "password": "vault://kv/business",
            }
        )
        assert findings == []

    def test_nested_secret_flagged(self) -> None:
        findings = find_plaintext_secrets({"credentials": {"client_secret": "super-secret-value"}})
        assert findings == ["credentials.client_secret"]

    def test_non_secret_keys_ignored(self) -> None:
        findings = find_plaintext_secrets({"timeout": 30, "base_url": "https://api.example.com", "rotate": True})
        assert findings == []

    def test_boolean_flags_not_flagged(self) -> None:
        findings = find_plaintext_secrets({"rotate_token": True})
        assert findings == []


class TestGatewayIdentityHeaders:
    def _identity(self):
        from hecate.tools.gateway.authz import CallerIdentity

        return CallerIdentity(
            workspace_id="ws-1",
            scope="workspace",
            authenticated=True,
            actor="apikey:7",
            on_behalf_of="user-2",
            delegation_id="dele-1",
            deployment_id="dealer-app",
        )

    def test_headers_from_verified_envelope_only(self) -> None:
        from hecate.tools.gateway.middleware import _identity_headers

        headers = _identity_headers(self._identity())
        assert headers["x-hecate-actor"] == "apikey:7"
        assert headers["x-hecate-on-behalf-of"] == "user-2"
        assert headers["x-hecate-delegation-id"] == "dele-1"
        assert headers["x-hecate-deployment"] == "dealer-app"

    def test_arguments_never_reach_identity_headers(self) -> None:
        # Even when the tool arguments try to inject identity headers, the
        # builder only reads the verified envelope.
        from hecate.tools.gateway.middleware import _identity_headers

        identity = self._identity()
        headers = _identity_headers(identity)
        assert "x-hecate-actor" in headers
        # The builder has no arguments input at all — injection is impossible.
        assert all(not k.startswith("x-hecate-args") for k in headers)

    async def test_executor_merges_identity_headers(self) -> None:
        import httpx

        from hecate.tools.gateway.executor import RestToolExecutor

        captured: dict = {}

        class _StubClient:
            async def request(self, method: str, url: str, **kwargs):
                captured["headers"] = dict(kwargs.get("headers") or {})
                return httpx.Response(200, json={"ok": True})

        class _Tool:
            name = "query"
            deleted = False

        class _Target:
            name = "t"
            base_url = "https://target.example.com"
            credentials = {"headers": {"authorization": "Bearer brokered"}}

        executor = RestToolExecutor(client=_StubClient())  # type: ignore[arg-type]
        import unittest.mock as mock

        with mock.patch(
            "hecate.tools.gateway.executor._extract_gateway_meta",
            return_value={"method": "POST", "path": "/query", "path_params": [], "body_params": []},
        ):
            result = await executor.execute(
                _Tool(),  # type: ignore[arg-type]
                _Target(),  # type: ignore[arg-type]
                {"sku": "1"},
                identity_headers={"x-hecate-actor": "apikey:7"},
            )
        assert result == {"ok": True}
        # Brokered credentials stay; verified identity rides its own headers.
        assert captured["headers"]["authorization"] == "Bearer brokered"
        assert captured["headers"]["x-hecate-actor"] == "apikey:7"
