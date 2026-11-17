"""Slack signing-secrets ``v0`` request verification — one shared implementation.

Slack signs each HTTP request as ``v0=<HMAC-SHA256(secret, "v0:<timestamp>:<body>")>``
and rejects requests whose timestamp strays outside a five-minute replay
window. Both consumers — the IM webhook path (``hecate-channel-slack``) and
the isolated Slack event-observation entry — must apply exactly the same
scheme, so the check lives here once and both sides delegate to it.

Reference: https://docs.slack.dev/authentication/verifying-requests-from-slack/
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping

# Slack's signing-secrets spec rejects requests whose signature basestring
# is older than five minutes (replay protection).
SIGNATURE_REPLAY_WINDOW_SECONDS = 300


def verify_slack_signature(
    signing_secret: str,
    headers: Mapping[str, str],
    raw_body: bytes,
    *,
    now: int | None = None,
) -> tuple[int, dict]:
    """Verify a Slack ``v0`` signature over the raw request body.

    Args:
        signing_secret: The app's signing secret.
        headers: Request headers (case-insensitive lookup for the two
            ``x-slack-*`` headers).
        raw_body: The exact bytes Slack sent — signatures never cover a
            re-serialized body.
        now: Current unix time; ``None`` uses the wall clock (tests inject
            a fixed time to exercise the replay window deterministically).

    Returns:
        ``(200, {})`` when the signature is valid and fresh; a ``(401,
        {"error": ...})`` payload describing the failure otherwise. The
        payload never echoes the body or the secret.
    """
    lower_headers = {name.lower(): value for name, value in headers.items()}
    signature = lower_headers.get("x-slack-signature")
    timestamp = lower_headers.get("x-slack-request-timestamp")
    if not signature or not timestamp:
        return 401, {"error": "missing X-Slack-Signature / X-Slack-Request-Timestamp headers"}
    if not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) > 12:
        return 401, {"error": "invalid X-Slack-Request-Timestamp header"}
    ts = int(timestamp)
    current = int(time.time()) if now is None else now
    if abs(current - ts) > SIGNATURE_REPLAY_WINDOW_SECONDS:
        return 401, {"error": "request timestamp outside the replay window"}
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + raw_body
    digest = hmac.new(signing_secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"v0={digest}".encode("ascii"), signature.encode("utf-8")):
        return 401, {"error": "signature verification failed"}
    return 200, {}
