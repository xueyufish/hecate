"""Automated trace backflow (7.2d).

Rules select scored production traces of an online evaluation task by
score-band filters and materialize them into evaluation datasets on explicit
trigger — the automated half of dataset backflow, complementing the human
annotation path (7.4) with shared, dataset-wide trace idempotency.
"""

from hecate.ops.evaluation.backflow.service import (
    BackflowRuleNotFoundError,
    BackflowService,
    BackflowValidationError,
)

__all__ = [
    "BackflowRuleNotFoundError",
    "BackflowService",
    "BackflowValidationError",
]
