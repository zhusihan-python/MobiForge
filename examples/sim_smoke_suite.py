#!/usr/bin/env python3
"""Run the built-in simulator smoke suite without a model server or device."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import load_sim_smoke_cases, run_sim_smoke_suite


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the simulator smoke suite")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable JSON report",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        help="Write the machine-readable JSON report to this path",
    )
    parser.add_argument(
        "--tasks-dir",
        type=str,
        help="Load simulator task fixtures from this directory instead of the built-ins",
    )
    args = parser.parse_args()

    cases = load_sim_smoke_cases(args.tasks_dir) if args.tasks_dir else None
    result = run_sim_smoke_suite(cases)
    if args.json_out:
        out_path = result.write_json(args.json_out)
        if not args.json:
            print(f"Wrote JSON report: {out_path}")

    if args.json:
        print(result.to_json())
        return

    print("Simulator smoke suite")
    print("=" * 24)
    print(f"Total: {result.total}")
    print(f"Passed: {result.passed}")
    print(f"Failed: {result.failed}")
    print(f"Success rate: {result.success_rate:.1%}")
    print(f"Average steps: {result.average_steps:.2f}")
    print()
    for case in result.cases:
        print(
            f"{case.case_id}: {case.status.value} "
            f"({case.steps_count} steps, {case.duration_ms} ms) - {case.message}"
        )


if __name__ == "__main__":
    main()
