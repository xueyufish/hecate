"""Independent readonly HTTP tool receiver for the cross-language pilot."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ToolCallbackServer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib handler interface
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                try:
                    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
                    if claims["aud"] != "pilot-tool-callback" or claims["sub"] != "pilot-ts-callback":
                        self.send_error(403)
                        return
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    owner.calls.append({"body": body, "claims": claims, "token": token})
                    text = body["input"].get("text", "")
                    result = (
                        {"status": "ok", "output": text, "served_by": "python-tool-receiver"}
                        if text
                        else {"status": "business_rejected", "reason": "empty echo text"}
                    )
                    raw = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (ValueError, KeyError, IndexError):
                    self.send_error(400)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/echo"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
