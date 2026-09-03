"""Studio plugin service — plugin lifecycle and configuration management.

Moved from ``src/hecate/services/plugin/`` (PR-C). This is the
plugin *service* half (CRUD + lifecycle), distinct from the plugin
extension SPI that lives in ``core/plugin/`` (the loader /
manifest / registry / spi / sdk / types / cli mechanism). The split
mirrors the tools/ pattern: definitions live in studio, extension
mechanism lives in core.
"""

from __future__ import annotations
