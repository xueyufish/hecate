"""Three-way consistency, part 1: every standard sample validates against its
authoritative schema file (task 1.2).

The schemas ARE the contract; samples are the executable evidence that the
contract is implementable. If a schema file and its samples drift apart, this
test fails.
"""

from __future__ import annotations

import jsonschema
import pytest

from tests.test_execution.conftest import (
    all_samples,
    load_sample,
    sample_schema_ref,
    validate_against_schema,
)


@pytest.mark.parametrize("path", all_samples(), ids=lambda p: p.name)
def test_sample_validates_against_authoritative_schema(path) -> None:
    ref = sample_schema_ref(path)
    if ref is None:
        pytest.skip("not governed by a single schema file (validated by dedicated binding tests)")
    schema_name, fragment = ref
    instance = load_sample(path)
    if path.name.startswith("negative-"):
        # Schema-level negatives (e.g. security-claims missing aud) MUST fail.
        with pytest.raises(jsonschema.ValidationError):
            validate_against_schema(instance, schema_name, fragment)
        return
    validate_against_schema(instance, schema_name, fragment)
