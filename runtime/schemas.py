"""Normalized runtime schemas (Phase 1, frozen at v1 by Gate A).

These dataclasses are the cross-backend contract: one ``AgentAdapter`` produces
``Action``\\ s, one ``EnvBackend`` produces ``Observation``\\ s, and the two
together compose a ``StepResult`` regardless of whether the backend is a real
ADB device or a simulation. See ``docs/schemas.md`` for the design rationale and
``docs/adr/0001-mobilegym-positioning.md`` for the contract decision.

Coordinate convention: ``Action.coordinates`` are normalized to ``[0, 1000]``
on both axes. This matches the Open-AutoGLM model output natively, so an
adapter does no coordinate math; the backend converts to pixels at execution
time (mirrors ``actions/handler.py::_convert_relative_to_absolute``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Union


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Size:
    """Pixel dimensions."""

    width: int
    height: int


@dataclass(frozen=True)
class DeviceMeta:
    """Backend-specific device metadata snapshot."""

    device_id: Optional[str] = None
    backend: Optional[str] = None  # "adb" | "hdc" | "ios" | "sim" | ...
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """What the agent saw before acting. Produced by ``EnvBackend.observe()``.

    ``env_state`` is optional by design: simulation backends populate it with a
    structured JSON snapshot; real-device (ADB) backends leave it ``None`` rather
    than fabricate fields (PRD Observation schema requirement).
    """

    id: str
    timestamp: str  # ISO-8601 UTC
    screenshot: Optional[str] = None  # artifact reference / data URL
    screenshot_size: Optional[Size] = None
    accessibility_tree: Optional[dict[str, Any]] = None
    env_state: Optional[dict[str, Any]] = None
    current_app: Optional[str] = None
    device: Optional[DeviceMeta] = None
    is_sensitive: bool = False


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------


class ActionType(str, Enum):
    """Normalized action set (Gate B, expanded to 12 to faithfully cover
    Open-AutoGLM's 14-action output space; still a subset of MobileGym's 18).

    The base 9 (tap/swipe/type_text/home/back/launch_app/wait/finish/
    request_user) are the PRD minimum. The 3 additions (double_tap/
    long_press/noop) preserve model capabilities the minimum set would lose:
    double-tap and long-press map to distinct gestures; noop covers the model's
    "record/summarize page" actions (Note/Call_API) that don't change the env.
    """

    TAP = "tap"
    DOUBLE_TAP = "double_tap"
    LONG_PRESS = "long_press"
    SWIPE = "swipe"
    TYPE_TEXT = "type_text"
    HOME = "home"
    BACK = "back"
    LAUNCH_APP = "launch_app"
    WAIT = "wait"
    FINISH = "finish"
    REQUEST_USER = "request_user"
    NOOP = "noop"


@dataclass(frozen=True)
class SafetyMeta:
    """Confirmation metadata for a sensitive action (PRD Story #16)."""

    reason: str
    confirmed: bool = False


@dataclass(frozen=True)
class Action:
    """A normalized action produced by ``AgentAdapter.act()``.

    ``coordinates`` are ``[x, y]`` for tap/long-press or ``[x1, y1, x2, y2]``
    for swipe, all normalized to ``[0, 1000]``. ``raw`` retains the adapter's
    original parsed action for trajectory provenance.
    """

    type: ActionType
    coordinates: Optional[list[float]] = None
    text: Optional[str] = None
    app: Optional[str] = None
    duration_ms: Optional[int] = None
    raw: Optional[dict[str, Any]] = None
    safety: Optional[SafetyMeta] = None

    @property
    def is_terminal(self) -> bool:
        """A FINISH action ends the run (success is judge-decided)."""
        return self.type is ActionType.FINISH

    @property
    def is_request_user(self) -> bool:
        """A REQUEST_USER action pauses the run into WAITING_USER."""
        return self.type is ActionType.REQUEST_USER


# ---------------------------------------------------------------------------
# Execution + judge results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvResult:
    """Outcome of ``EnvBackend.execute(action)``.

    Mirrors the existing ``phone_agent.actions.ActionResult`` so the future
    ``AdbDeviceEnv`` can wrap it without translation.
    """

    success: bool
    should_finish: bool = False
    message: Optional[str] = None


JudgeType = Literal["none", "rule", "state", "vlm"]


@dataclass(frozen=True)
class JudgeResult:
    """Verdict from a ``Judge``."""

    passed: bool
    reason: str
    score: Optional[float] = None
    evidence: Optional[dict[str, Any]] = None
    judge_type: JudgeType = "none"


# ---------------------------------------------------------------------------
# Step + run lifecycle
# ---------------------------------------------------------------------------


StepStatus = Literal["ok", "error", "waiting_user"]


@dataclass
class ResumePoint:
    """Resumption data for a ``WAITING_USER`` pause (PRD: resumable, not terminal).

    Phase 1 may not implement true human handoff, but the fields must exist so a
    paused trajectory can be resumed or cancelled later without a schema change.
    """

    resume_token: str
    pending_action: Action
    paused_at_step: int
    user_response: Optional[str] = None  # None while paused; set on resume


@dataclass(frozen=True)
class StepResult:
    """One runner iteration. The explicit unit persisted per trajectory step.

    This replaces the legacy recorder's reliance on ``getattr`` over
    ``phone_agent.StepResult``: any adapter/backend pair must populate the same
    fields, so this is the contract.
    """

    step: int
    observation: Observation
    action: Optional[Action] = None
    model_output: Optional[str] = None
    env_result: Optional[EnvResult] = None
    judge_result: Optional[JudgeResult] = None
    status: StepStatus = "ok"
    error: Optional[dict[str, Any]] = None
    resume_point: Optional[ResumePoint] = None  # present iff status == waiting_user
    duration_ms: int = 0


class RunStatus(str, Enum):
    """Terminal (or paused) run states.

    Lowercase values, distinct from the legacy spike's uppercase ``TaskStatus``,
    so an oracle comparison between old and new paths cannot conflate the two.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    WAITING_USER = "waiting_user"

    @property
    def is_terminal(self) -> bool:
        """WAITING_USER is paused/resumable, not terminal (PRD)."""
        return self is not RunStatus.WAITING_USER


# ---------------------------------------------------------------------------
# Task spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeRef:
    """Serializable reference to a judge, resolved by a ``JudgeRegistry``.

    No ``Callable`` lives in a serialized task spec; the registry returns the
    real ``Judge`` at run time. Callers may still pass concrete ``Judge``
    instances directly when task specs are constructed in process.
    """

    judge_type: JudgeType
    entrypoint: Optional[str] = None  # dotted registry key
    config: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class FreeformTask:
    """Natural-language smoke task (PRD Story #12)."""

    description: str
    max_steps: Optional[int] = None
    timeout_seconds: Optional[float] = None


@dataclass(frozen=True)
class VerifiableTask:
    """Task with setup + a judge reference (PRD Story #5, #6)."""

    description: str
    judge_ref: Optional[JudgeRef] = None
    setup: Optional[dict[str, Any]] = None  # state to inject (sim)
    setup_actions: Optional[list[Action]] = None  # device prep actions (ADB)
    goal: Optional[str] = None
    constraints: list[str] = field(default_factory=list)
    answer_schema: Optional[dict[str, Any]] = None  # AnswerSheet
    max_steps: Optional[int] = None
    timeout_seconds: Optional[float] = None


TaskSpec = Union[FreeformTask, VerifiableTask]
