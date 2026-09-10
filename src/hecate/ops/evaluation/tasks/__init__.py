"""Evaluation task services (7.2c Online/Offline Evaluation Tasks).

- :mod:`service` — task definition CRUD, enable/disable lifecycle, offline
  run triggering, target-typed score queries
- :mod:`runner` — async offline run execution (job lifecycle)
- :mod:`trace_input` — session event-log → ``EvalInput`` projection
- :mod:`online_worker` — always-on production-trace sampling + scoring loop
"""

from hecate.ops.evaluation.tasks.service import (
    EvaluationTaskNotFoundError,
    EvaluationTaskService,
    EvaluationTaskValidationError,
)

__all__ = [
    "EvaluationTaskNotFoundError",
    "EvaluationTaskService",
    "EvaluationTaskValidationError",
]
