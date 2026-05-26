"""Rate limiting middleware for API endpoints.

Provides per-API-key rate limiting using an in-memory counter.
Default limit is 60 requests per minute (configurable via ``RATE_LIMIT_RPM``).
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Annotated

from fastapi import Depends, HTTPException, status

from hecate.core.config import settings
from hecate.core.deps import verify_api_key


class RateLimiter:
    """In-memory rate limiter tracking requests per API key.

    Uses a sliding window approach with per-second granularity.
    """

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """Check if a request is allowed for the given key.

        Args:
            key: The API key to check.

        Returns:
            bool: True if request is allowed, False if rate limit exceeded.
        """
        now = time.time()
        window_start = now - 60

        self.requests[key] = [t for t in self.requests[key] if t > window_start]

        if len(self.requests[key]) >= self.requests_per_minute:
            return False

        self.requests[key].append(now)
        return True

    def get_retry_after(self, key: str) -> int:
        """Get the number of seconds until the next request is allowed.

        Args:
            key: The API key to check.

        Returns:
            int: Seconds until next allowed request.
        """
        if not self.requests[key]:
            return 0

        oldest = self.requests[key][0]
        return max(1, int(oldest + 60 - time.time()))


rate_limiter = RateLimiter(requests_per_minute=settings.RATE_LIMIT_RPM)


async def check_rate_limit(
    api_key: Annotated[str, Depends(verify_api_key)],
) -> str:
    """FastAPI dependency that enforces rate limiting.

    Args:
        api_key: The validated API key.

    Returns:
        str: The API key if rate limit is not exceeded.

    Raises:
        HTTPException: 429 if rate limit is exceeded, with Retry-After header.
    """
    if not rate_limiter.is_allowed(api_key):
        retry_after = rate_limiter.get_retry_after(api_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit exceeded. Try again in {retry_after} seconds.",
                    "details": None,
                }
            },
            headers={"Retry-After": str(retry_after)},
        )
    return api_key
