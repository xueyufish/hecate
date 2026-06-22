"""Built-in evaluators package.

Importing this package triggers auto-registration of all built-in
evaluators via the ``@register_evaluator`` decorator.
"""

from __future__ import annotations

from hecate.services.evaluation.evaluators import agent as _agent  # noqa: F401
from hecate.services.evaluation.evaluators import citation as _citation  # noqa: F401
from hecate.services.evaluation.evaluators import content as _content  # noqa: F401
from hecate.services.evaluation.evaluators import format as _format  # noqa: F401
from hecate.services.evaluation.evaluators import judge as _judge  # noqa: F401
from hecate.services.evaluation.evaluators import multi_turn as _multi_turn  # noqa: F401
from hecate.services.evaluation.evaluators import programmatic as _programmatic  # noqa: F401
from hecate.services.evaluation.evaluators import rag as _rag  # noqa: F401
from hecate.services.evaluation.evaluators import safety as _safety  # noqa: F401
from hecate.services.evaluation.evaluators import tool as _tool  # noqa: F401
