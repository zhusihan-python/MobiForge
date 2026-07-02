"""Batch execution helpers for small verifiable task suites."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from runtime.boundary import AgentAdapter, EnvBackend, Judge, Runner, RunResult
from runtime.schemas import RunStatus, TaskSpec


@dataclass(frozen=True)
class TaskCase:
    """One runnable task-suite case.

    ``task`` remains the serializable runtime spec. ``metadata`` is intentionally
    in-process-only: fixtures can put scripts, tags, or backend hints here
    without changing the persisted task schema.
    """

    id: str
    task: TaskSpec
    judge: Optional[Judge] = None
    tags: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TaskCaseResult:
    """Outcome for one task-suite case."""

    case_id: str
    status: RunStatus
    message: str
    steps_count: int
    duration_ms: int
    passed: bool
    run_result: RunResult

    def to_report_dict(self) -> dict:
        """Return a compact JSON-serializable case report."""
        return _case_report(self)


@dataclass(frozen=True)
class TaskSuiteResult:
    """Aggregate summary for a task-suite run."""

    cases: list[TaskCaseResult]

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def success_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def average_steps(self) -> float:
        return (
            sum(case.steps_count for case in self.cases) / self.total
            if self.total
            else 0.0
        )

    @property
    def average_duration_ms(self) -> float:
        return (
            sum(case.duration_ms for case in self.cases) / self.total
            if self.total
            else 0.0
        )

    @property
    def failed_cases(self) -> list[TaskCaseResult]:
        return [case for case in self.cases if not case.passed]

    def to_report_dict(self, generated_at: Optional[str] = None) -> dict:
        """Return a JSON-serializable suite report.

        This is a compact batch report, not a full trajectory export. It keeps
        enough structured data for run-to-run comparison and failure triage,
        while full per-step artifacts remain the responsibility of a future
        ``TrajectoryStore``.
        """
        timestamp = generated_at or datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": 1,
            "generated_at": timestamp,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "success_rate": self.success_rate,
                "average_steps": self.average_steps,
                "average_duration_ms": self.average_duration_ms,
            },
            "cases": [case.to_report_dict() for case in self.cases],
        }

    def to_json(self, *, indent: int = 2, generated_at: Optional[str] = None) -> str:
        """Serialize the suite report as JSON text."""
        return json.dumps(
            self.to_report_dict(generated_at=generated_at),
            ensure_ascii=False,
            indent=indent,
        )

    def write_json(
        self,
        path: str | Path,
        *,
        indent: int = 2,
        generated_at: Optional[str] = None,
    ) -> Path:
        """Write the suite report to ``path`` and return the resolved path."""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            self.to_json(indent=indent, generated_at=generated_at) + "\n",
            encoding="utf-8",
        )
        return out_path


def _case_report(case: TaskCaseResult) -> dict:
    run = case.run_result
    final_judge = run.final_judge
    last_step = run.steps[-1] if run.steps else None
    last_obs = last_step.observation if last_step is not None else None
    return {
        "case_id": case.case_id,
        "passed": case.passed,
        "status": case.status.value,
        "message": case.message,
        "steps_count": case.steps_count,
        "duration_ms": case.duration_ms,
        "final_judge": (
            {
                "passed": final_judge.passed,
                "reason": final_judge.reason,
                "score": final_judge.score,
                "evidence": final_judge.evidence,
                "judge_type": final_judge.judge_type,
            }
            if final_judge is not None
            else None
        ),
        "last_observation": (
            {
                "id": last_obs.id,
                "current_app": last_obs.current_app,
                "env_state": last_obs.env_state,
                "is_sensitive": last_obs.is_sensitive,
            }
            if last_obs is not None
            else None
        ),
    }


class TaskSuiteRunner:
    """Run a batch of task cases through the shared ``Runner``.

    Factories receive the case so different cases can select different adapter
    scripts, backend initial states, or judge configurations while still using
    the same runtime loop.
    """

    def __init__(
        self,
        *,
        adapter_factory: Callable[[TaskCase], AgentAdapter],
        backend_factory: Callable[[TaskCase], EnvBackend],
        max_steps: int = 100,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.adapter_factory = adapter_factory
        self.backend_factory = backend_factory
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    def run(self, cases: Iterable[TaskCase]) -> TaskSuiteResult:
        return TaskSuiteResult([self.run_case(case) for case in cases])

    def run_case(self, case: TaskCase) -> TaskCaseResult:
        result = Runner(
            self.adapter_factory(case),
            self.backend_factory(case),
            judge=case.judge,
            max_steps=self.max_steps,
            timeout_seconds=self.timeout_seconds,
        ).run(case.task)
        return TaskCaseResult(
            case_id=case.id,
            status=result.status,
            message=result.message,
            steps_count=result.steps_count,
            duration_ms=result.duration_ms,
            passed=result.status is RunStatus.SUCCEEDED,
            run_result=result,
        )
