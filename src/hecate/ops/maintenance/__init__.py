"""Lifecycle maintenance jobs for the application host.

Hosts the periodic jobs that survive the M1-T1 retirement of the
legacy meta-agent suite: the compliance checker and the garbage
collector, plus the interval scheduler that runs them. They are
started and stopped by the composition root
(``core/composition/wiring.py``) and enabled per deployment via
``META_AGENTS_ENABLED``. Import directly from submodules.
"""

from __future__ import annotations
