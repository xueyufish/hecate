"""Run a versioned repeated Controller two-host experiment.

Run manually with
``python -m tests.scenarios.baseline.run_controller_host_experiment``.
The built-in definition repeats the full deterministic-injection matrix; a
real-model experiment requires a definition file that pins the model
(``--definition``), the real-mode gate and the provider credential. The
aggregate record and every trial record land in ``tests/scenarios/baselines/``
as new immutable files; the hosts' transient work directories live under
``--state-root`` and are never committed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from tests.scenarios.baseline import controller_host_experiment as experiment


def main(argv: list[str] | None = None) -> Path | None:
    """CLI entry: run the definition's repetition matrix and write records."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--definition",
        default=None,
        help="path to an experiment definition JSON (default: the built-in deterministic definition)",
    )
    parser.add_argument(
        "--print-default-definition",
        action="store_true",
        help="print the built-in definition JSON as a starting point and exit",
    )
    parser.add_argument(
        "--state-root",
        default=None,
        help="directory for the trials' transient host work dirs (default: a fresh temp directory)",
    )
    parser.add_argument(
        "--trial-report-dir",
        default=None,
        help="directory for the committed trial records (default: tests/scenarios/baselines)",
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="directory for the committed aggregate record (default: tests/scenarios/baselines)",
    )
    parser.add_argument(
        "--pacing-seconds",
        type=float,
        default=25.0,
        help="pause between real cases (free-tier rate limits; never applied to hermetic trials)",
    )
    args = parser.parse_args(argv)
    if args.print_default_definition:
        print(json.dumps(experiment.build_definition(repetitions=3), indent=2, ensure_ascii=False))
        return None

    definition = (
        experiment.load_definition(Path(args.definition))
        if args.definition
        else experiment.build_definition(repetitions=3)
    )
    state_root = (
        Path(args.state_root) if args.state_root else Path(tempfile.mkdtemp(prefix="controller-host-experiment-"))
    )
    report = asyncio.run(
        experiment.run_experiment(
            definition=definition,
            state_root=state_root,
            trial_report_dir=Path(args.trial_report_dir) if args.trial_report_dir else experiment.BASELINE_DIR,
            case_pacing_seconds=args.pacing_seconds,
        )
    )
    out = experiment.write_experiment_report(
        report, Path(args.report_dir) if args.report_dir else experiment.BASELINE_DIR
    )
    counts = report["aggregate"]["denominators"]
    print(
        f"{report['_meta']['status']}: trials recorded/skipped/failed = "
        f"{counts['trials_recorded']}/{counts['trials_skipped']}/{counts['trials_failed']}"
    )
    print(f"real-model quality conclusion: {report['_meta']['conclusion']['real_model_quality']}")
    print(f"experiment aggregate written to {out}")
    return out


if __name__ == "__main__":
    main()
