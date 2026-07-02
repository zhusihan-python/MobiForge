"""Batch execution helpers for small verifiable task suites."""

from __future__ import annotations

from dataclasses import dataclass, field
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
