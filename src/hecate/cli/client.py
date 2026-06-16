"""HTTP client wrapper for CLI.

Provides a thin wrapper around httpx for communicating with the Hecate API.
Handles authentication, error formatting, and JWT auto-refresh.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx
from rich.console import Console

from hecate.cli.config import get_profile, set_profile_value

console = Console(stderr=True)

DEFAULT_TIMEOUT = 30.0


class HecateClient:
    """Synchronous HTTP client for the Hecate REST API.

    Args:
        profile_name: Name of the config profile to use.
        timeout: Request timeout in seconds.
    """

    def __init__(self, profile_name: str = "default", timeout: float = DEFAULT_TIMEOUT) -> None:
        self.profile = get_profile(profile_name)
        self.base_url: str = self.profile.get("base_url", "http://localhost:8000").rstrip("/")
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        """Build auth headers from profile config.

        Returns:
            Dict with Authorization header.
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}

        # Prefer JWT access_token over API key
        access_token = self.profile.get("access_token", "")
        api_key = self.profile.get("api_key", "")

        token = access_token if access_token else api_key
        if token:
            headers["Authorization"] = f"Bearer {token}"

        return headers

    def _handle_response(self, response: httpx.Response) -> dict[str, Any] | list[Any] | None:
        """Parse response JSON or raise on error.

        Args:
            response: The httpx response object.

        Returns:
            Parsed JSON response data.

        Raises:
            SystemExit: On 4xx/5xx errors.
        """
        if response.status_code >= 400:
            try:
                error_data = response.json()
                error = error_data.get("error", {})
                code = error.get("code", "UNKNOWN")
                message = error.get("message", response.text)
            except Exception:
                code = f"HTTP_{response.status_code}"
                message = response.text or f"HTTP {response.status_code}"

            console.print(f"[red]Error:[/red] {code} — {message}")
            sys.exit(1)

        if response.status_code == 204:
            return None

        return response.json()

    def _refresh_token(self) -> bool:
        """Attempt to refresh JWT access token.

        Returns:
            True if refresh succeeded, False otherwise.
        """
        refresh_token = self.profile.get("refresh_token", "")
        if not refresh_token:
            return False

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/api/auth/refresh",
                    json={"refresh_token": refresh_token},
                )
                if response.status_code == 200:
                    data = response.json()
                    self.profile["access_token"] = data.get("access_token", "")
                    new_refresh = data.get("refresh_token", "")
                    if new_refresh:
                        self.profile["refresh_token"] = new_refresh
                    # Persist refreshed tokens
                    set_profile_value("default", "access_token", self.profile["access_token"])
                    if new_refresh:
                        set_profile_value("default", "refresh_token", new_refresh)
                    return True
        except Exception:
            # Refresh failure is non-fatal; the next request will get 401
            return False

        return False

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """Make an API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            path: API path (e.g., /api/agents).
            json: JSON body for POST/PUT requests.
            params: Query parameters.
            files: Multipart files for upload.
            content_type: Override Content-Type header.

        Returns:
            Parsed JSON response.
        """
        headers = self._get_headers()
        if content_type:
            headers["Content-Type"] = content_type
        elif files:
            headers.pop("Content-Type", None)

        url = f"{self.base_url}{path}"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json,
                    params=params,
                    files=files,
                )

                # Auto-refresh on 401
                if response.status_code == 401 and self._refresh_token():
                    headers = self._get_headers()
                    response = client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        json=json,
                        params=params,
                        files=files,
                    )

                return self._handle_response(response)

        except httpx.ConnectError:
            console.print(f"[red]Error:[/red] Cannot connect to Hecate server at {self.base_url}")
            sys.exit(1)
        except httpx.TimeoutException:
            console.print(f"[red]Error:[/red] Request timed out after {self.timeout}s")
            sys.exit(1)

    def stream_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Make a streaming API request (for SSE).

        Args:
            method: HTTP method.
            path: API path.
            json: JSON body.

        Yields:
            SSE data lines as they arrive.
        """
        headers = self._get_headers()
        url = f"{self.base_url}{path}"

        with (
            httpx.Client(timeout=None) as client,  # noqa: S113
            client.stream(method=method, url=url, headers=headers, json=json) as response,
        ):
            if response.status_code >= 400:
                # Read full response for error
                error_text = response.read().decode()
                console.print(f"[red]Error:[/red] HTTP {response.status_code} — {error_text}")
                sys.exit(1)

            for line in response.iter_lines():
                line = line.strip()
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    yield data

    # Convenience methods
    def get(self, path: str, **kwargs: Any) -> Any:
        """GET request."""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        """POST request."""
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        """PUT request."""
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        """DELETE request."""
        return self.request("DELETE", path, **kwargs)
