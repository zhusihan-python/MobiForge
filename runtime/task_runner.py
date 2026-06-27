"""Task state machine around the upstream phone-agent step API."""

import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from runtime.run_recorder import RunRecorder


class TaskStatus(str, Enum):
    """Lifecycle states for one task execution."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


@dataclass
class TaskRunResult:
    """Final task outcome returned to CLI or future API callers."""

    status: TaskStatus
    message: str
    steps: int
    duration_ms: int
    run_dir: Path | None = None


class TaskRunner:
    """Execute an agent task with recording, cancellation, and timeout."""

    def __init__(
        self,
        agent: Any,
        recorder: RunRecorder | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.agent = agent
        self.recorder = recorder
        self.timeout_seconds = timeout_seconds
        self.metadata = metadata or {}
        self.status = TaskStatus.PENDING
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cooperative cancellation between agent steps."""
        self._cancel_event.set()

    def run(self, task: str) -> TaskRunResult:
        """Run a task until completion, failure, cancellation, or timeout."""
        started_at = time.perf_counter()
        steps = 0
        run_dir = self.recorder.start(task, self.metadata) if self.recorder else None
        self.status = TaskStatus.RUNNING
        self.agent.reset()

        max_steps = self.agent.agent_config.max_steps
        message = "Max steps reached"

        try:
            for step_number in range(1, max_steps + 1):
                terminal = self._pre_step_terminal_state(started_at)
                if terminal:
                    self.status, message = terminal
                    break

                step_result = self.agent.step(task if step_number == 1 else None)
                steps = step_number
                if self.recorder:
                    self.recorder.record_step(step_number, step_result)

                if step_result.finished:
                    self.status = (
                        TaskStatus.SUCCEEDED
                        if step_result.success
                        else TaskStatus.FAILED
                    )
                    message = step_result.message or (
                        "Task completed"
                        if step_result.success
                        else "Task failed"
                    )
                    break
            else:
                self.status = TaskStatus.FAILED
        except KeyboardInterrupt:
            self.status = TaskStatus.CANCELLED
            message = "Task interrupted"
        except Exception as exc:
            self.status = TaskStatus.FAILED
            message = f"Task error: {exc}"

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        result = TaskRunResult(
            status=self.status,
            message=message,
            steps=steps,
            duration_ms=duration_ms,
            run_dir=run_dir,
        )
        if self.recorder:
            self.recorder.finish(
                {
                    "status": result.status.value,
                    "message": result.message,
                    "steps": result.steps,
                    "duration_ms": result.duration_ms,
                }
            )
        return result

    def _pre_step_terminal_state(
        self, started_at: float
    ) -> tuple[TaskStatus, str] | None:
        if self._cancel_event.is_set():
            return TaskStatus.CANCELLED, "Task cancelled"
        if (
            self.timeout_seconds is not None
            and time.perf_counter() - started_at >= self.timeout_seconds
        ):
            return TaskStatus.TIMEOUT, "Task timed out"
        return None
