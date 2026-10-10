"""Business-API tool adapters: ``query_inventory`` read and
``submit_inventory_update`` write (durable profile).

Dispatches allowlisted tools to the business App's HTTP API using the
server-verified principal and domain scope — the request body's self-reported
claims never reach this layer. The business API owns its rules (domain
isolation, roles, one-shot parameter-bound approvals); the runner only
classifies the response into contract error semantics
(``unsupported``/``authorization``/``business``/``transient``/``unknown``).
The write tool is admitted only by the durable profile, where every dispatch
flows through the persistent action ledger first.
"""

from __future__ import annotations


class BusinessApiToolDispatcher:
    """Calls the business API for allowlisted tools over httpx."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        if tool_name == "query_inventory":
            payload_args = {key: arguments[key] for key in ("domain", "sku") if key in arguments}
            return await self._post("/inventory/query", payload_args, principal, domains)
        if tool_name == "submit_inventory_update":
            payload_args = {key: arguments[key] for key in ("domain", "sku", "quantity") if key in arguments}
            return await self._post("/inventory/write", payload_args, principal, domains)
        if tool_name == "submit_inventory_adjustment":
            # Second protected write: lets acceptance runs put two protected
            # dispatches in one run so the lease-renewal boundary is real
            # (one lease authorizes one protected action).
            payload_args = {key: arguments[key] for key in ("domain", "sku", "quantity") if key in arguments}
            return await self._post("/inventory/write", payload_args, principal, domains)
        return {"status": "unsupported", "detail": f"tool {tool_name!r} has no business-API mapping"}

    async def _post(self, path: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        import httpx

        payload = {"principal": principal, "domains": domains, "arguments": arguments}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self._base_url}{path}", json=payload)
        except httpx.TimeoutException:
            return {"status": "unknown", "detail": "business API timeout; outcome not established"}
        except httpx.HTTPError as exc:
            return {"status": "transient", "detail": f"business API unreachable: {exc}"}

        if response.status_code == 200:
            body = response.json()
            return {"status": "ok", "result": body.get("result"), "outcome": body.get("outcome", "ok")}
        if response.status_code in (401, 403):
            return {"status": "authorization", "detail": response.json().get("detail", "denied by business API")}
        if response.status_code == 422:
            return {"status": "business", "detail": response.json().get("detail", "business rejection")}
        if response.status_code == 503:
            return {"status": "transient", "detail": "business API unavailable"}
        return {"status": "unknown", "detail": f"unexpected business API status {response.status_code}"}
