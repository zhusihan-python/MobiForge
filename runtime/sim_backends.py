"""Lightweight simulation backends for architecture and judge development.

``SimulatedDeviceEnv`` is intentionally tiny: it is not MobileGym and does not
try to emulate UI physics. It gives the runtime a structured-state backend that
can run through the same ``Runner`` contract as ADB, so task/judge/trajectory
work can move forward without a real phone or the heavy MobileGym dependency.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from runtime.boundary import EnvBackend
from runtime.schemas import (
    Action,
    ActionType,
    DeviceMeta,
    EnvResult,
    Observation,
    Size,
    TaskSpec,
)


_NON_EXECUTABLE = {
    ActionType.NOOP,
    ActionType.FINISH,
    ActionType.REQUEST_USER,
}


class SimulatedDeviceEnv(EnvBackend):
    """In-memory mobile environment with structured ``env_state``.

    The state shape is deliberately permissive JSON-like data. By convention the
    backend maintains:

    - ``current_app``: current simulated app name.
    - ``history``: app navigation stack used by ``BACK``.
    - ``text_input``: latest text typed by ``TYPE_TEXT``.
    - ``events``: normalized action events executed in this run.

    ``VerifiableTask.setup`` can overlay any of these fields, or add task-owned
    state. This gives Phase 3 a deterministic backend before any MobileGym
    adapter is introduced.
    """

    def __init__(
        self,
        initial_state: Optional[dict[str, Any]] = None,
        *,
        device_id: str = "sim-1",
        screen_size: Optional[Size] = None,
        screenshot: Optional[str] = None,
    ) -> None:
        self.initial_state = deepcopy(initial_state) if initial_state else {}
        self.device_id = device_id
        self.screen_size = screen_size or Size(width=1000, height=1000)
        self.screenshot = screenshot
        self.state: dict[str, Any] = {}
        self._obs_count = 0
        self.reset()

    def reset(self) -> None:
        self.state = {
            "current_app": "home",
            "history": [],
            "text_input": "",
            "events": [],
        }
        _deep_update(self.state, deepcopy(self.initial_state))
        self._obs_count = 0

    def apply_task_setup(self, task: TaskSpec) -> None:
        setup = getattr(task, "setup", None)
        if isinstance(setup, dict):
            _deep_update(self.state, deepcopy(setup))

    def observe(self) -> Observation:
        self._obs_count += 1
        return Observation(
            id=f"sim-{self._obs_count}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            screenshot=self.screenshot,
            screenshot_size=self.screen_size,
            env_state=deepcopy(self.state),
            current_app=str(self.state.get("current_app", "")),
            device=DeviceMeta(device_id=self.device_id, backend="sim"),
            is_sensitive=bool(self.state.get("is_sensitive", False)),
        )

    def execute(self, action: Action) -> EnvResult:
        if action.type in _NON_EXECUTABLE:
            raise ValueError(
                f"Action type {action.type!r} must not reach EnvBackend.execute(); "
                f"the Runner short-circuits it."
            )

        if action.type is ActionType.LAUNCH_APP:
            return self._launch_app(action)
        if action.type is ActionType.HOME:
            return self._home(action)
        if action.type is ActionType.BACK:
            return self._back(action)
        if action.type is ActionType.TYPE_TEXT:
            return self._type_text(action)

        self._record_event(action)
        return EnvResult(success=True)

    def _launch_app(self, action: Action) -> EnvResult:
        if not action.app:
            return EnvResult(success=False, message="No app specified")
        self._push_history()
        self.state["current_app"] = action.app
        self._record_event(action)
        return EnvResult(success=True)

    def _home(self, action: Action) -> EnvResult:
        self._push_history()
        self.state["current_app"] = "home"
        self._record_event(action)
        return EnvResult(success=True)

    def _back(self, action: Action) -> EnvResult:
        history = self.state.setdefault("history", [])
        self.state["current_app"] = history.pop() if history else "home"
        self._record_event(action)
        return EnvResult(success=True)

    def _type_text(self, action: Action) -> EnvResult:
        self.state["text_input"] = action.text or ""
        self._record_event(action)
        return EnvResult(success=True)

    def _push_history(self) -> None:
        current = self.state.get("current_app")
        if current:
            self.state.setdefault("history", []).append(current)

    def _record_event(self, action: Action) -> None:
        event = {
            "type": action.type.value,
            "coordinates": list(action.coordinates) if action.coordinates else None,
            "text": action.text,
            "app": action.app,
            "duration_ms": action.duration_ms,
        }
        self.state["last_action"] = event
        self.state.setdefault("events", []).append(event)


def _deep_update(target: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively overlay ``update`` onto ``target``."""
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
    return target
