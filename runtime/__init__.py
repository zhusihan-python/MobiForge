"""Execution runtime for observable phone-agent tasks.

Legacy spike (still in use by ``main.py`` / ``PhoneAgent``):
    RunRecorder, TaskRunResult, TaskRunner, TaskStatus

Phase 1 boundary (Gate A): the new adapter/backend/judge/store contracts and
the boundary-aware Runner live alongside the spike so the two can be compared as
an oracle during the ``PhoneAgent.step()`` migration (ADR-0001).
"""

from importlib import import_module

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
from runtime.task_suite import (
    TaskCase,
    TaskCaseResult,
    TaskSuiteResult,
    TaskSuiteRunner,
)

_LAZY_EXPORTS = {
    "AdbDeviceEnv": ("runtime.env_backends", "AdbDeviceEnv"),
    "OpenAutoGLMAdapter": ("runtime.adapters", "OpenAutoGLMAdapter"),
    "ScriptedAgentAdapter": ("runtime.sim_suite", "ScriptedAgentAdapter"),
    "SimulatedDeviceEnv": ("runtime.sim_backends", "SimulatedDeviceEnv"),
    "StateJudge": ("runtime.judges", "StateJudge"),
    "make_sim_smoke_cases": ("runtime.sim_suite", "make_sim_smoke_cases"),
    "run_sim_smoke_suite": ("runtime.sim_suite", "run_sim_smoke_suite"),
}

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
    "ScriptedAgentAdapter",
    "Size",
    "SimulatedDeviceEnv",
    "StateJudge",
    "TaskCase",
    "TaskCaseResult",
    "StepResult",
    "TaskSuiteResult",
    "TaskSuiteRunner",
    "VerifiableTask",
    "make_sim_smoke_cases",
    "run_sim_smoke_suite",
]


def __getattr__(name):
    """Lazily expose optional runtime integrations.

    Core schemas and the boundary runner are importable without model or device
    dependencies. Adapter/backend implementations load only when explicitly
    requested.
    """
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
