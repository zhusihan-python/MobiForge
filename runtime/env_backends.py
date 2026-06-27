"""Environment backends: implement ``EnvBackend`` over real device control.

``AdbDeviceEnv`` wraps the existing ``device_factory`` (observation capture) and
``phone_agent.actions.ActionHandler`` (action execution, including the
[0,1000] -> pixel coordinate conversion) behind the normalized ``EnvBackend``
interface. It is a faithful wrapper, not a reimplementation: it reverse-maps a
normalized ``Action`` to the legacy action dict and delegates to
``ActionHandler.execute(dict, width, height)`` so there is exactly one source of
truth for ADB execution.

Key contract (see Gate B Step 2b plan):

- ``observe()`` must run before ``execute()`` — ``execute()`` needs the screen
  size captured at observe time for coordinate conversion; calling it first
  raises ``RuntimeError``.
- The reverse mapping rebuilds the legacy dict from normalized fields. It does
  **not** read ``action.raw`` — ``raw`` is trajectory provenance only, never an
  execution shortcut (keeps the normalized contract as the single execution path).
- ``ActionHandler`` exceptions become ``EnvResult(success=False, message=...)``
  so the Runner records the step instead of hitting the generic Task-error path.
- Coordinates pass through with ``int(value)`` — no clamping; faithful to model
  output. Validation/clamping is a later, uniformly-applied concern.
- ``reset()`` is intentionally a no-op (legacy-compatible); app/device reset
  policy belongs to a later hardening phase, not this wrapper slice.
- ``health()``/``close()`` are minimal (``True`` / no-op); device-pool liveness
  is out of scope here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from phone_agent.actions import ActionHandler
from phone_agent.device_factory import get_device_factory
from runtime.boundary import EnvBackend
from runtime.schemas import (
    Action,
    ActionType,
    DeviceMeta,
    EnvResult,
    Observation,
    Size,
)


# Normalized ActionType -> legacy action-name string consumed by ActionHandler.
_ACTION_TYPE_TO_NAME = {
    ActionType.TAP: "Tap",
    ActionType.DOUBLE_TAP: "Double Tap",
    ActionType.LONG_PRESS: "Long Press",
    ActionType.TYPE_TEXT: "Type",
    ActionType.SWIPE: "Swipe",
    ActionType.BACK: "Back",
    ActionType.HOME: "Home",
    ActionType.LAUNCH_APP: "Launch",
    ActionType.WAIT: "Wait",
}

# Types that never reach execute() — the Runner short-circuits them. If one
# slips through, fail loud rather than silently call the handler.
_NON_EXECUTABLE = {
    ActionType.NOOP,
    ActionType.FINISH,
    ActionType.REQUEST_USER,
}


class AdbDeviceEnv(EnvBackend):
    """Real Android/HDC device as an ``EnvBackend``.

    ``device_factory`` and ``action_handler`` are injectable (defaulting to the
    global factory and a fresh ``ActionHandler``) so the same backend can be
    unit-tested with fakes and run against a real device unchanged.
    """

    def __init__(
        self,
        device_factory=None,
        action_handler=None,
        device_id: Optional[str] = None,
    ):
        self.device_factory = device_factory if device_factory is not None else get_device_factory()
        self.action_handler = action_handler if action_handler is not None else ActionHandler(
            device_id=device_id
        )
        self.device_id = device_id
        # Cached from observe(); execute() needs these for coordinate conversion.
        self._width: Optional[int] = None
        self._height: Optional[int] = None
        self._obs_count = 0

    # -- EnvBackend ----------------------------------------------------------

    def reset(self) -> None:
        """Intentionally a no-op.

        Legacy ``PhoneAgent`` performs no device reset between runs. App/device
        reset policy (clearing app data, relaunching, etc.) belongs to a later
        ``AdbDeviceEnv`` hardening phase, not this wrapper slice.
        """
        self._width = None
        self._height = None
        self._obs_count = 0

    def observe(self) -> Observation:
        screenshot = self.device_factory.get_screenshot(self.device_id)
        current_app = self.device_factory.get_current_app(self.device_id)

        self._width = screenshot.width
        self._height = screenshot.height
        self._obs_count += 1

        return Observation(
            id=f"adb-{self._obs_count}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            screenshot=screenshot.base64_data,
            screenshot_size=Size(width=screenshot.width, height=screenshot.height),
            current_app=current_app,
            device=DeviceMeta(device_id=self.device_id, backend="adb"),
            is_sensitive=screenshot.is_sensitive,
            # env_state stays None: ADB cannot read structured app state.
        )

    def execute(self, action: Action) -> EnvResult:
        if self._width is None or self._height is None:
            raise RuntimeError("observe() must be called before execute()")
        if action.type in _NON_EXECUTABLE:
            raise ValueError(
                f"Action type {action.type!r} must not reach EnvBackend.execute(); "
                f"the Runner short-circuits it."
            )

        legacy = _reverse_map(action)
        try:
            result = self.action_handler.execute(legacy, self._width, self._height)
        except Exception as e:  # noqa: BLE001 - keep the step's StepResult populated
            return EnvResult(success=False, message=f"Action failed: {e}")

        # Transparent passthrough: keep message None on success (no "ok") so the
        # legacy-vs-new oracle comparison shows no spurious diff.
        return EnvResult(
            success=result.success,
            should_finish=result.should_finish,
            message=result.message,
        )

    def health(self) -> bool:
        # Minimal: device-pool liveness is out of scope for this wrapper slice.
        return True

    def close(self) -> None:
        # Minimal no-op; the global device_factory owns connection lifecycle.
        return None


def _reverse_map(action: Action) -> dict[str, Any]:
    """Build the legacy action dict from normalized fields.

    Never reads ``action.raw``: ``raw`` is provenance only; the normalized
    contract is the single execution path.
    """
    name = _ACTION_TYPE_TO_NAME.get(action.type)
    if name is None:
        raise ValueError(f"No reverse mapping for action type {action.type!r}")

    coords = action.coordinates
    # Every dict fed to ActionHandler.execute() MUST carry "_metadata": "do" —
    # that is the protocol's first check (handler.py:59); without it the handler
    # returns "Unknown action type: None" before dispatching to Tap/Back/etc.
    out: dict[str, Any] = {"_metadata": "do", "action": name}

    if action.type in (ActionType.TAP, ActionType.DOUBLE_TAP, ActionType.LONG_PRESS):
        out["element"] = [_as_int(coords, 0), _as_int(coords, 1)]
    elif action.type is ActionType.TYPE_TEXT:
        out["text"] = action.text or ""
    elif action.type is ActionType.SWIPE:
        out["start"] = [_as_int(coords, 0), _as_int(coords, 1)]
        out["end"] = [_as_int(coords, 2), _as_int(coords, 3)]
    elif action.type is ActionType.LAUNCH_APP:
        out["app"] = action.app
    elif action.type is ActionType.WAIT:
        out["duration"] = _ms_to_seconds_str(action.duration_ms)
    # BACK / HOME need only the action name (already set).
    return out


def _as_int(coords: Optional[list[float]], idx: int) -> int:
    """Plain int cast, no clamping — faithful to model output."""
    return int(coords[idx]) if coords is not None and idx < len(coords) else 0


def _ms_to_seconds_str(duration_ms: Optional[int]) -> str:
    """``None -> "1 seconds"`` (legacy default); ``3000 -> "3 seconds"``.

    ``ActionHandler._handle_wait`` parses this with ``float(s.replace("seconds",""))``,
    so the integer form round-trips exactly.
    """
    if duration_ms is None:
        return "1 seconds"
    seconds = duration_ms / 1000
    # Use int when whole to avoid float-format fragility, else float.
    return f"{int(seconds)} seconds" if seconds.is_integer() else f"{seconds} seconds"
