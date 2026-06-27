"""Execution runtime for observable phone-agent tasks.

Legacy spike (still in use by ``main.py`` / ``PhoneAgent``):
    RunRecorder, TaskRunResult, TaskRunner, TaskStatus

Phase 1 boundary (Gate A): the new adapter/backend/judge/store contracts and
the boundary-aware Runner live alongside the spike so the two can be compared as
an oracle during the ``PhoneAgent.step()`` migration (ADR-0001).
"""

from runtime.adapters import OpenAutoGLMAdapter
from runtime.boundary import (
    AgentAdapter,
    EnvBackend,
    InMemoryTrajectoryStore,
    Judge,
    NoneJudge,
    Runner,
    RunResult,
    TrajectoryStore,
)
from runtime.env_backends import AdbDeviceEnv
from runtime.run_recorder import RunRecorder
from runtime.schemas import (
    Action,
    ActionType,
    DeviceMeta,
    EnvResult,
    FreeformTask,
    JudgeRef,
    JudgeResult,
    Observation,
    ResumePoint,
    RunStatus,
    SafetyMeta,
    Size,
    StepResult,
    VerifiableTask,
)
from runtime.task_runner import TaskRunResult, TaskRunner, TaskStatus

__all__ = [
    # Legacy spike
    "RunRecorder",
    "TaskRunResult",
    "TaskRunner",
    "TaskStatus",
    # Phase 1 boundary
    "AgentAdapter",
    "AdbDeviceEnv",
    "EnvBackend",
    "InMemoryTrajectoryStore",
    "Judge",
    "NoneJudge",
    "OpenAutoGLMAdapter",
    "Runner",
    "RunResult",
    "TrajectoryStore",
    # Phase 1 schemas
    "Action",
    "ActionType",
    "DeviceMeta",
    "EnvResult",
    "FreeformTask",
    "JudgeRef",
    "JudgeResult",
    "Observation",
    "ResumePoint",
    "RunStatus",
    "SafetyMeta",
    "Size",
    "StepResult",
    "VerifiableTask",
]
