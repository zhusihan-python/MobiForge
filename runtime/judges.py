"""Built-in deterministic judges for verifiable tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from runtime.boundary import Judge
from runtime.schemas import Action, EnvResult, JudgeResult, Observation


_MISSING = object()


@dataclass(frozen=True)
class StateJudge(Judge):
    """Judge success by matching a subset of ``Observation.env_state``.

    This is the deterministic simulation-primary judge described in the PRD.
    It intentionally treats ``expected_state`` as a recursive subset: task specs
    can assert only the fields that matter for success while ignoring diagnostic
    state such as event logs.
    """

    expected_state: dict[str, Any]

    def evaluate(
        self,
        observation: Observation,
        action: Optional[Action],
        env_result: Optional[EnvResult],
    ) -> JudgeResult:
        if observation.env_state is None:
            return JudgeResult(
                passed=False,
                reason="observation has no env_state",
                evidence={"expected_state": self.expected_state},
                judge_type="state",
            )

        match = _match_subset(self.expected_state, observation.env_state)
        if match.passed:
            return JudgeResult(
                passed=True,
                reason="expected state matched",
                evidence={
                    "expected_state": self.expected_state,
                    "observed_state": observation.env_state,
                },
                judge_type="state",
            )

        return JudgeResult(
            passed=False,
            reason=(
                f"state mismatch at {match.path}: "
                f"expected {match.expected!r}, got {match.actual!r}"
            ),
            evidence={
                "expected_state": self.expected_state,
                "observed_state": observation.env_state,
                "path": match.path,
            },
            judge_type="state",
        )


@dataclass(frozen=True)
class _SubsetMatch:
    passed: bool
    path: str = "$"
    expected: Any = None
    actual: Any = None


def _match_subset(expected: Any, actual: Any, path: str = "$") -> _SubsetMatch:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return _SubsetMatch(False, path, expected, actual)
        for key, expected_value in expected.items():
            actual_value = actual.get(key, _MISSING)
            if actual_value is _MISSING:
                return _SubsetMatch(False, f"{path}.{key}", expected_value, None)
            child = _match_subset(expected_value, actual_value, f"{path}.{key}")
            if not child.passed:
                return child
        return _SubsetMatch(True)

    if expected == actual:
        return _SubsetMatch(True)
    return _SubsetMatch(False, path, expected, actual)
