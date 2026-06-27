"""Boundary interfaces and the boundary-aware runner (Phase 1, Gate A).

This module defines the seam the PRD requires:

    Runner -> EnvBackend.observe() --Observation--> AgentAdapter.act() --Action-->
      EnvBackend.execute() --EnvResult--> Judge --JudgeResult--> TrajectoryStore

The ABCs mirror MobileGym's confirmed ``BaseMobileEnv`` / ``BaseAgent`` shape
(see ``docs/research-spike-mobilegym.md``). Gate A proves the schemas are
backend-agnostic with fake implementations; Gate B (next batch) threads a real
``PhoneAgent.step()``-derived adapter and an ADB backend through the same
``Runner``.

Phase 1 passes concrete ``Judge`` instances; the serialized ``JudgeRef`` +
``JudgeRegistry`` indirection is deferred until task specs need to be persisted.
"""

from __future__ import annotations

import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional

from runtime.schemas import (
    Action,
    ActionType,
    EnvResult,
    JudgeResult,
    Observation,
    ResumePoint,
    RunStatus,
    StepResult,
    TaskSpec,
)


# ---------------------------------------------------------------------------
# EnvBackend
# ---------------------------------------------------------------------------


class EnvBackend(ABC):
    """Real or simulated mobile environment.

    Owns observation capture and action execution. ``observe()`` lives here,
    never on the adapter — the adapter *consumes* an Observation; it does not
    capture one. Structured ``env_state`` is optional: simulation backends
    populate it, real-device backends return ``None`` (mirrors MobileGym's
    ``supports_state_injection`` flag).
    """

    @abstractmethod
    def reset(self) -> None:
        """Reset to the task's initial state before a run."""

    @abstractmethod
    def observe(self) -> Observation:
        """Capture the current screen/environment state."""

    @abstractmethod
    def execute(self, action: Action) -> EnvResult:
        """Execute a normalized action. Coordinates arrive in [0,1000]."""

    def health(self) -> bool:
        """Return whether the backend is healthy enough to continue."""
        return True

    def close(self) -> None:
        """Release resources. Default no-op."""


# ---------------------------------------------------------------------------
# AgentAdapter
# ---------------------------------------------------------------------------


class AgentAdapter(ABC):
    """Converts an upstream model/agent into observe -> act.

    Owns prompt construction, the model call, and parsing model output into a
    normalized ``Action``. Does NOT own observation capture or action execution.
    Mirrors MobileGym's ``BaseAgent.act``.

    Response lifecycle (Gate B 2a.1): ``act()`` produces an ``Action`` and stashes
    the underlying model response; the Runner records ``last_model_output`` into
    the ``StepResult`` and, after execution, calls ``commit()`` so the adapter
    can append the assistant turn to its context. This keeps the new path
    oracle-equivalent to the legacy ``plan -> execute -> append_assistant`` order
    without the adapter touching execution.
    """

    @abstractmethod
    def reset(self, task: str) -> None:
        """Reset adapter state for a new task (e.g. clear context)."""

    @abstractmethod
    def act(self, observation: Observation) -> Action:
        """Decide the next action from the current observation."""

    @property
    def last_model_output(self) -> Optional[str]:
        """Raw model output from the most recent ``act()`` (for trajectory
        recording). Default ``None``; adapters that hold a model response
        override this."""
        return None

    def commit(self, step_result: "StepResult") -> None:
        """Record the step outcome back into the adapter (e.g. append the
        assistant turn after execution). Default no-op; adapters that maintain a
        conversation context override this."""
        return None


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------


class Judge(ABC):
    """Evaluates whether a step advanced or completed the task."""

    @abstractmethod
    def evaluate(
        self,
        observation: Observation,
        action: Optional[Action],
        env_result: Optional[EnvResult],
    ) -> JudgeResult:
        ...


class NoneJudge(Judge):
    """Manual smoke runs. Always passes once the agent emits FINISH.

    Preserves the legacy "success == model emits finish" behavior for
    ``FreeformTask`` runs without an explicit judge.
    """

    def evaluate(
        self,
        observation: Observation,
        action: Optional[Action],
        env_result: Optional[EnvResult],
    ) -> JudgeResult:
        return JudgeResult(
            passed=True,
            reason="no judge configured",
            judge_type="none",
        )


# ---------------------------------------------------------------------------
# TrajectoryStore
# ---------------------------------------------------------------------------


class TrajectoryStore(ABC):
    """Persists a run's trajectory. Evolved from the spike's RunRecorder."""

    @abstractmethod
    def start(
        self,
        run_id: str,
        task: TaskSpec,
        backend_meta: dict[str, Any],
        agent_meta: dict[str, Any],
    ) -> None:
        ...

    @abstractmethod
    def record_step(self, step: StepResult) -> None:
        ...

    @abstractmethod
    def finish(
        self,
        status: RunStatus,
        final_judge: Optional[JudgeResult] = None,
        resume_point: Optional[ResumePoint] = None,
    ) -> None:
        ...


class InMemoryTrajectoryStore(TrajectoryStore):
    """Captures a run in memory. Sufficient to prove schema equivalence (Gate A)."""

    def __init__(self) -> None:
        self.steps: list[StepResult] = []
        self.run_id: Optional[str] = None
        self.task: Optional[TaskSpec] = None
        self.backend_meta: dict[str, Any] = {}
        self.agent_meta: dict[str, Any] = {}
        self.final_status: Optional[RunStatus] = None
        self.final_judge: Optional[JudgeResult] = None
        self.resume_point: Optional[ResumePoint] = None

    def start(
        self,
        run_id: str,
        task: TaskSpec,
        backend_meta: dict[str, Any],
        agent_meta: dict[str, Any],
    ) -> None:
        self.run_id = run_id
        self.task = task
        self.backend_meta = dict(backend_meta)
        self.agent_meta = dict(agent_meta)
        self.steps = []

    def record_step(self, step: StepResult) -> None:
        self.steps.append(step)

    def finish(
        self,
        status: RunStatus,
        final_judge: Optional[JudgeResult] = None,
        resume_point: Optional[ResumePoint] = None,
    ) -> None:
        self.final_status = status
        self.final_judge = final_judge
        self.resume_point = resume_point


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    """Final outcome of one task run."""

    status: RunStatus
    message: str
    steps_count: int
    duration_ms: int
    steps: list[StepResult]
    final_judge: Optional[JudgeResult] = None
    resume_point: Optional[ResumePoint] = None


class _StepOutcome(NamedTuple):
    """Internal per-step result + loop control from ``Runner._run_step``.

    ``stop`` is set when the step is terminal (FINISH), pauses (REQUEST_USER),
    or failed execution — i.e. the run loop must not continue to the next step.
    """

    step_result: StepResult
    stop: bool


class Runner:
    """Drives one task through the observe -> act -> execute -> judge loop.

    Backend-agnostic: the same ``Runner`` runs the same adapter against any
    ``EnvBackend``. This is the unit Gate A proves schema-equivalent across two
    structurally different fake backends.

    Loop semantics:

    - ``FINISH``    -> terminal. Status is judge-decided when a judge is present
      (passed => SUCCEEDED, else FAILED); without a judge, SUCCEEDED (legacy
      "model emitted finish" smoke behavior).
    - ``REQUEST_USER`` -> WAITING_USER (paused, resumable), with a ResumePoint.
    - any other action -> ``backend.execute()``; StepResult.status follows
      ``env_result.success``.
    - max-steps exhausted -> FAILED.
    - timeout (checked before each step) -> TIMEOUT.
    """

    def __init__(
        self,
        adapter: AgentAdapter,
        backend: EnvBackend,
        *,
        judge: Optional[Judge] = None,
        store: Optional[TrajectoryStore] = None,
        max_steps: int = 100,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.adapter = adapter
        self.backend = backend
        # judge is never None at runtime: a missing judge defaults to NoneJudge,
        # so there is exactly ONE "no judge" behavior (review P2#1). Callers that
        # want verifiable evaluation pass an explicit Rule/State/VLM Judge.
        self.judge: Judge = judge if judge is not None else NoneJudge()
        self.store = store
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    def run(self, task: TaskSpec, task_description: Optional[str] = None) -> RunResult:
        """Run a task to a terminal or paused state."""
        description = task_description or _task_description(task)
        run_id = secrets.token_hex(4)
        max_steps = _effective_max_steps(task, self.max_steps)
        timeout = _effective_timeout(task, self.timeout_seconds)

        if self.store is not None:
            self.store.start(
                run_id,
                task,
                backend_meta={"backend": type(self.backend).__name__},
                agent_meta={"adapter": type(self.adapter).__name__},
            )

        self.adapter.reset(description)
        self.backend.reset()

        started_at = time.perf_counter()
        steps: list[StepResult] = []
        status = RunStatus.FAILED
        message = "Max steps reached"
        final_judge: Optional[JudgeResult] = None
        resume_point: Optional[ResumePoint] = None

        try:
            for step_number in range(1, max_steps + 1):
                # Pre-step checks: timeout.
                if timeout is not None and time.perf_counter() - started_at >= timeout:
                    status, message = RunStatus.TIMEOUT, "Task timed out"
                    break

                outcome = self._run_step(step_number)
                step_result = outcome.step_result
                steps.append(step_result)
                if self.store is not None:
                    self.store.record_step(step_result)

                action = step_result.action

                # REQUEST_USER -> pause. Promote the *exact* ResumePoint minted
                # in _run_step (single token, shared by step and run result).
                if step_result.resume_point is not None:
                    resume_point = step_result.resume_point
                    status, message = (
                        RunStatus.WAITING_USER,
                        action.text or "Waiting for user input",
                    )
                    break

                # Execution failure -> terminal FAILED (review P1#1).
                if step_result.status == "error":
                    status, message = (
                        RunStatus.FAILED,
                        f"Step {step_number} execution failed: "
                        f"{(step_result.error or {}).get('message', 'unknown')}",
                    )
                    break

                # FINISH -> terminal, judge-decided.
                if action is not None and action.is_terminal:
                    final_judge = step_result.judge_result or (
                        self.judge.evaluate(
                            step_result.observation, action, step_result.env_result
                        )
                        if self.judge is not None
                        else None
                    )
                    if final_judge is not None:
                        status = (
                            RunStatus.SUCCEEDED
                            if final_judge.passed
                            else RunStatus.FAILED
                        )
                        message = (
                            action.text
                            or final_judge.reason
                            or ("Task completed" if final_judge.passed else "Task failed")
                        )
                    else:
                        # No judge: legacy smoke success on finish.
                        status, message = (
                            RunStatus.SUCCEEDED,
                            action.text or "Task completed",
                        )
                    break

                # Non-terminal step: if the adapter signaled stop without a
                # terminal/paused/error reason, something is inconsistent.
                if outcome.stop:
                    status, message = RunStatus.FAILED, "Step stopped unexpectedly"
                    break
            else:
                # Loop exhausted without a terminal action.
                status, message = RunStatus.FAILED, "Max steps reached"
        except KeyboardInterrupt:
            status, message = RunStatus.CANCELLED, "Task interrupted"
        except Exception as exc:  # noqa: BLE001 - surface as a diagnosable failure
            status, message = RunStatus.FAILED, f"Task error: {exc}"
        finally:
            self.backend.close()

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        if self.store is not None:
            self.store.finish(status, final_judge, resume_point)

        return RunResult(
            status=status,
            message=message,
            steps_count=len(steps),
            duration_ms=duration_ms,
            steps=steps,
            final_judge=final_judge,
            resume_point=resume_point,
        )

    def _run_step(self, step_number: int) -> _StepOutcome:
        """One observe -> act -> execute -> judge iteration.

        Returns a ``_StepOutcome`` carrying the ``StepResult`` plus loop-control
        flags so ``run()`` can stop on terminal/paused/failed steps without
        re-deriving them. The ``ResumePoint`` (if any) is created **here, once**,
        and the same object is promoted to run-level state — there is never a
        second token minted for the same pause (review P1#2).
        """
        step_started_at = time.perf_counter()

        observation = self.backend.observe()
        action = self.adapter.act(observation)
        model_output = self.adapter.last_model_output

        env_result: Optional[EnvResult] = None
        judge_result: Optional[JudgeResult] = None
        step_status: str = "ok"
        error: Optional[dict[str, Any]] = None
        resume_point: Optional[ResumePoint] = None
        stop = False  # set True for FINISH / REQUEST_USER / execution failure

        if action is not None and action.is_terminal:
            # FINISH: no execution; judge (if any) decides success.
            if self.judge is not None:
                judge_result = self.judge.evaluate(observation, action, None)
            stop = True
        elif action is not None and action.is_request_user:
            # REQUEST_USER: no execution; pause. One ResumePoint, minted here.
            step_status = "waiting_user"
            resume_point = ResumePoint(
                resume_token=secrets.token_hex(8),
                pending_action=action,
                paused_at_step=step_number,
            )
            stop = True
        elif action is not None and action.type is ActionType.NOOP:
            # NOOP: "no environment change" is runtime semantics, not
            # backend-specific — short-circuit without calling the backend (every
            # backend would otherwise have to implement it identically).
            env_result = EnvResult(success=True, message="noop")
        else:
            env_result = self.backend.execute(action) if action is not None else None
            if env_result is not None and not env_result.success:
                # Execution failure ends the run as FAILED immediately (review
                # P1#1): a failed action means the task cannot proceed.
                step_status = "error"
                error = {"message": env_result.message or "execution failed"}
                stop = True
            elif self.judge is not None:
                judge_result = self.judge.evaluate(observation, action, env_result)

        step_result = StepResult(
            step=step_number,
            observation=observation,
            action=action,
            model_output=model_output,
            env_result=env_result,
            judge_result=judge_result,
            status=step_status,
            error=error,
            resume_point=resume_point,
            duration_ms=int((time.perf_counter() - step_started_at) * 1000),
        )
        # Record the assistant turn back into the adapter after execution. This
        # yields *final* planner-context equivalence with the legacy path (image
        # stripped + assistant appended); it is NOT a strict step-order match
        # (legacy strips before execute, the new path strips here after).
        self.adapter.commit(step_result)
        return _StepOutcome(step_result=step_result, stop=stop)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_description(task: TaskSpec) -> str:
    return getattr(task, "description", str(task))


def _effective_max_steps(task: TaskSpec, default: int) -> int:
    override = getattr(task, "max_steps", None)
    return override if isinstance(override, int) and override > 0 else default


def _effective_timeout(task: TaskSpec, default: Optional[float]) -> Optional[float]:
    override = getattr(task, "timeout_seconds", None)
    return override if isinstance(override, (int, float)) and override > 0 else default
