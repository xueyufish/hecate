"""Three-way consistency, part 1: every standard sample validates against its
authoritative schema file (task 1.2).

The schemas ARE the contract; samples are the executable evidence that the
contract is implementable. If a schema file and its samples drift apart, this
test fails.
"""

from __future__ import annotations

import pytest

from tests.test_execution.conftest import (
    all_samples,
    load_sample,
    sample_schema_ref,
    validate_against_schema,
)


@pytest.mark.parametrize("path", all_samples(), ids=lambda p: p.name)
def test_sample_validates_against_authoritative_schema(path) -> None:
    schema_name, fragment = sample_schema_ref(path)
    validate_against_schema(load_sample(path), schema_name, fragment)
