"""Resolve serializable ``JudgeRef`` values into executable judges."""

from __future__ import annotations

from typing import Callable, Optional

from runtime.boundary import Judge, NoneJudge
from runtime.judges import StateJudge
from runtime.schemas import JudgeRef, TaskSpec


JudgeFactory = Callable[[JudgeRef], Judge]


class JudgeRegistry:
    """Registry for resolving persisted judge references at run time."""

    def __init__(self) -> None:
        self._factories: dict[str, JudgeFactory] = {}

    def register(self, entrypoint: str, factory: JudgeFactory) -> None:
        """Register a judge factory under a stable entrypoint key."""
        if not entrypoint:
            raise ValueError("entrypoint must be non-empty")
        self._factories[entrypoint] = factory

    def resolve(self, ref: JudgeRef) -> Judge:
        """Resolve ``ref`` to a concrete ``Judge`` instance."""
        entrypoint = ref.entrypoint or _default_entrypoint(ref)
        factory = self._factories.get(entrypoint)
        if factory is None:
            raise KeyError(f"No judge registered for entrypoint {entrypoint!r}")
        return factory(ref)


def build_default_judge_registry() -> JudgeRegistry:
    """Build a registry with core built-in judges."""
    registry = JudgeRegistry()
    registry.register("none", lambda ref: NoneJudge())
    registry.register("runtime.judges.NoneJudge", lambda ref: NoneJudge())
    registry.register("state", _state_judge_factory)
    registry.register("runtime.judges.StateJudge", _state_judge_factory)
    return registry


def resolve_task_judge(
    task: TaskSpec,
    registry: Optional[JudgeRegistry] = None,
) -> Optional[Judge]:
    """Resolve ``task.judge_ref`` when present.

    ``FreeformTask`` and ``VerifiableTask`` without a ``judge_ref`` return
    ``None`` so callers can keep their existing "no explicit judge" behavior.
    """
    ref = getattr(task, "judge_ref", None)
    if ref is None:
        return None
    return (registry or DEFAULT_JUDGE_REGISTRY).resolve(ref)


def _default_entrypoint(ref: JudgeRef) -> str:
    if ref.judge_type == "none":
        return "none"
    if ref.judge_type == "state":
        return "state"
    raise KeyError(
        f"Judge type {ref.judge_type!r} has no built-in default; "
        "provide judge_ref.entrypoint and register it"
    )


def _state_judge_factory(ref: JudgeRef) -> StateJudge:
    config = ref.config or {}
    expected_state = config.get("expected_state")
    if not isinstance(expected_state, dict):
        raise ValueError("StateJudge config requires expected_state: dict")
    return StateJudge(expected_state)


DEFAULT_JUDGE_REGISTRY = build_default_judge_registry()
