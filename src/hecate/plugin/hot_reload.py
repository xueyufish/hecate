"""Hot-reload for plugins during development.

Uses watchdog to monitor the plugins directory. When a plugin.yaml
or Python source file changes, the affected plugin is reloaded.
Only active when settings.HOT_RELOAD is True.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver

logger = logging.getLogger(__name__)


class PluginFileHandler:
    """File system event handler that triggers plugin reload."""

    def __init__(self, reload_callback: object) -> None:
        self._callback = reload_callback

    def on_modified(self, event: object) -> None:
        if not hasattr(event, "src_path"):
            return
        path = Path(event.src_path)
        if path.name == "plugin.yaml" or path.suffix == ".py":
            logger.info("Plugin file changed: %s — triggering reload", path)
            if asyncio.iscoroutinefunction(self._callback):
                asyncio.ensure_future(self._callback(path))
            elif callable(self._callback):
                self._callback(path)


class PluginHotReloader:
    """Watches a plugins directory and reloads changed plugins."""

    def __init__(self, plugins_dir: str, reload_callback: object) -> None:
        self._plugins_dir = Path(plugins_dir)
        self._callback = reload_callback
        self._observer: BaseObserver | None = None

    def start(self) -> None:
        try:
            from watchdog.observers import Observer

            handler = PluginFileHandler(self._callback)
            self._observer = Observer()
            self._observer.schedule(handler, str(self._plugins_dir), recursive=True)
            self._observer.start()
            logger.info("Hot-reload enabled for %s", self._plugins_dir)
        except ImportError:
            logger.warning("watchdog not installed — hot-reload disabled")

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("Hot-reload stopped")
