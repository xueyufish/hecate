"""Tier-2 baseline runner (P03 slice) — RECORDING tool, not a test.

Runs the minimal retrieval-correctness dataset against the synthetic corpus
with a deterministic rubric and records the result as JSON. Deliberately not
named ``test_*`` so pytest never collects it; CI must not execute this file.

Usage (from the repo root):

    python -m tests.scenarios.baseline.run_baseline

The output file (``tests/scenarios/baselines/single_agent_baseline.json``)
is a RECORD, not a gate: it carries no pass/fail authority and its header
marks business value as unverified. Evaluator identity is recorded via the
rubric version and the evaluation-module format reused below
(``hecate.ops.evaluation.types`` EvalInput/Score). Offline/online evaluation
splitting, external evaluators, and publish-gate consumption are step10/step11
deliverables.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from hecate.ops.evaluation.types import EvalInput, Score
from tests.scenarios.tools.corpus_index import CorpusIndex

BASELINE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASELINE_DIR.parent / "baselines" / "single_agent_baseline.json"
RUBRIC_VERSION_KEY = "rubric_version"

COST_BASELINE_SCHEMA: dict[str, Any] = {
    "status": "not-collected",
    "reason": (
        "the deterministic rubric run performs no model call, so no usage "
        "exists to record; this is a fact, not a zero-cost measurement"
    ),
    "gate": (
        "a real-model cost baseline is blocked on G4 (reported/estimated/"
        "reconciled usage accounting) — target step7/step10 of the plan"
    ),
    # Fields a model-backed run must record, per the platform-scenario-pack
    # spec. reported = provider-returned usage; estimated = locally derived
    # and never presented as a final cost.
    "fields_when_collected": [
        "token_prompt",
        "token_completion",
        "token_total",
        "token_cached",
        "usage_source",  # reported | estimated
        "price_version",
        "currency",
        "cost_amount",
        "latency_ms_total",
    ],
}


def evaluate_item(item: dict[str, Any], index: CorpusIndex) -> list[Score]:
    """Score one dataset item with the deterministic retrieval rubric.

    Builds the standard ``EvalInput`` first (the evaluation module's shared
    input shape) and derives both scores from it:

    - ``citation_correctness`` — share of expected citations present in the
      returned citations metadata (empty expectations score 1.0);
    - ``acl_compliance`` — 1.0 when every retrieved context came from a
      document the principal may read, else 0.0.
    """
    hits = index.search(item["query"], item["principal"])
    expected = {(c["doc_id"], c["anchor"]) for c in item["expected_citations"]}
    returned = {(hit.doc_id, hit.anchor) for hit in hits}

    eval_input = EvalInput(
        query=item["query"],
        retrieved_contexts=[hit.text for hit in hits],
        metadata={
            "principal": item["principal"],
            "item_id": item["id"],
            "returned_citations": sorted(f"{doc}#{anchor}" for doc, anchor in returned),
            "acl_violations": sorted(
                hit.doc_id
                for hit in hits
                if item["principal"] not in index.doc(hit.doc_id).get("acl", {}).get("read", [])
            ),
        },
    )
    returned_citations = {tuple(ref.split("#", 1)) for ref in eval_input.metadata["returned_citations"]}

    citation_value = (len(expected & returned_citations) / len(expected)) if expected else 1.0
    acl_value = 0.0 if eval_input.metadata["acl_violations"] else 1.0

    return [
        Score(metric_name="citation_correctness", value=citation_value, source="deterministic"),
        Score(metric_name="acl_compliance", value=acl_value, source="deterministic"),
    ]


def main() -> Path:
    dataset = yaml.safe_load((BASELINE_DIR / "dataset_p03.yaml").read_text(encoding="utf-8"))
    index = CorpusIndex()

    per_item: dict[str, Any] = {}
    metric_totals: dict[str, list[float]] = {}
    for item in dataset["items"]:
        scores = evaluate_item(item, index)
        per_item[item["id"]] = {
            "query": item["query"],
            "principal": item["principal"],
            "scores": [{"metric": s.metric_name, "value": s.value, "source": s.source} for s in scores],
        }
        for s in scores:
            metric_totals.setdefault(s.metric_name, []).append(s.value)

    summary = {
        metric: {"mean": sum(values) / len(values), "n": len(values)} for metric, values in metric_totals.items()
    }

    report = {
        "_meta": {
            "kind": "record",
            "gate": False,
            "business_value": "unverified",
            "note": "Tier-2 recorded baseline; never executed by CI; not a pass/fail authority.",
            "rubric_version": dataset[RUBRIC_VERSION_KEY],
            "evaluator_format": "hecate.ops.evaluation.types (EvalInput/Score, source=deterministic)",
            "dataset": "tests/scenarios/baseline/dataset_p03.yaml",
            "cost_baseline": dict(COST_BASELINE_SCHEMA),
        },
        "summary": summary,
        "items": per_item,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"baseline recorded: {OUTPUT_PATH}")
    return OUTPUT_PATH


if __name__ == "__main__":
    main()
