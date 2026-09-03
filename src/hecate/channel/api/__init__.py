"""Channel HTTP API surface (v1).

Moved from ``src/hecate/api/v1/`` during Phase R-complete. The v1
namespace is the public OpenAI-compatible endpoint group:

- ``chat`` — ``POST /v1/chat/completions``
- ``models`` — ``GET /v1/models``
- ``agents`` — ``POST /v1/agents/...`` family
- ``channels`` — ``GET/POST /v1/channels/...`` webhook router
- ``im_bindings`` — ``POST /v1/im-bindings/...`` binding workflow

Mounted by ``main.py`` at the ``/v1`` prefix; see ``channel.api.v1.*``
modules.
"""

from __future__ import annotations
