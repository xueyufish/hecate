"""Memory services for Hecate Agent platform.

This module provides memory management capabilities:

- **WorkingMemoryService** — L1 working memory: named context blocks
- **CompressionPipeline** — L2 conversation compression (snip→microcompact→autocompact)
- **UserMemoryService** — L3 user memory: persistent facts with vector retrieval
"""

from __future__ import annotations
