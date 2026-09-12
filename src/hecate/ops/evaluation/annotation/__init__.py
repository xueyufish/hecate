"""Human annotation queues (7.4) and human score calibration (7.4a).

The annotation service manages reviewer worklists over production traces
and writes human scores into the shared target-typed score ledger; the
calibration service quantifies machine-vs-human agreement over paired
samples. Score storage itself stays in ``evaluation_task_scores`` (see the
``evaluation-tasks`` capability).
"""

from hecate.ops.evaluation.annotation.calibration import (
    CalibrationService,
    CalibrationValidationError,
)
from hecate.ops.evaluation.annotation.service import (
    AnnotationItemStateError,
    AnnotationQueueItemNotFoundError,
    AnnotationQueueNotFoundError,
    AnnotationService,
    AnnotationValidationError,
)

__all__ = [
    "AnnotationItemStateError",
    "AnnotationQueueNotFoundError",
    "AnnotationQueueItemNotFoundError",
    "AnnotationService",
    "AnnotationValidationError",
    "CalibrationService",
    "CalibrationValidationError",
]
