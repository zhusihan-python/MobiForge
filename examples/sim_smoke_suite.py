#!/usr/bin/env python3
"""Run the built-in simulator smoke suite without a model server or device."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import run_sim_smoke_suite


def main() -> None:
    result = run_sim_smoke_suite()
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
