"""Agent adapters: wrap an upstream model into the normalized observe -> act seam.

``OpenAutoGLMAdapter`` composes the extracted ``ModelPlanner`` + ``ActionParser``
(see ``phone_agent/planner.py``) to implement ``AgentAdapter.act()``. It owns the
mapping from Open-AutoGLM's 14-action output space to the 12 normalized
``ActionType`` values. Observation capture and action execution live on the
``EnvBackend``; the adapter only consumes an Observation and produces an Action.

Mapping (full table in ``docs/schemas.md``; every Action carries ``raw`` = the
original parsed dict for trajectory provenance):

    Tap/Double Tap/Long Press   -> TAP/DOUBLE_TAP/LONG_PRESS (coordinates)
    Type/Type_Name              -> TYPE_TEXT (text)
    Swipe                       -> SWIPE (coordinates, 4)
    Back/Home                   -> BACK/HOME
    Launch                      -> LAUNCH_APP (app)
    Wait                        -> WAIT (duration_ms parsed from "x seconds")
    Take_over/Interact          -> REQUEST_USER (text)
    Note/Call_API               -> NOOP (raw=dict; no env change)
    finish                      -> FINISH (text)

Unknown action names raise (the model's action space is known/closed).
"""

from __future__ import annotations

from typing import Any, Optional

from runtime.boundary import AgentAdapter
from runtime.schemas import Action, ActionType, Observation


class OpenAutoGLMAdapter(AgentAdapter):
    """Wraps Open-AutoGLM into the normalized adapter boundary.

    Built from an already-constructed ``ModelPlanner`` and ``ActionParser``
    (injected) so this adapter adds no new model/parse responsibility — it is
    the thin ``act()`` mapping layer ADR-0001 calls for, modeled on MobileGym's
    ``autoglm`` adapter.
    """

    def __init__(self, planner, parser):
        self.planner = planner
        self.parser = parser
        self._task: Optional[str] = None
        # Stashed model response from the last act(); surfaced via
        # last_model_output and consumed by commit() to append the assistant turn.
        self._last_response = None

    def reset(self, task: str) -> None:
        self._task = task
        self._last_response = None
        self.planner.reset()

    def act(self, observation: Observation) -> Action:
        """Decide the next action: plan -> parse -> map. Model errors propagate.

        The model response is stashed (not returned) so the Runner can record
        ``last_model_output`` and call ``commit()`` after execution to append the
        assistant turn — mirroring the legacy plan->execute->append order.
        """
        is_first = self.planner.context_len() == 0
        response = self.planner.plan(
            screenshot_base64=observation.screenshot or "",
            current_app=observation.current_app or "",
            user_prompt=self._task,
            is_first=is_first,
        )
        self._last_response = response
        action_dict = self.parser.parse(response.action)
        return self._map_action(action_dict)

    @property
    def last_model_output(self) -> Optional[str]:
        return self._last_response.raw_content if self._last_response else None

    def commit(self, step_result) -> None:
        """Append the assistant turn reconstructed from the last model response.

        Called by the Runner *after* execution (or after a terminal/paused
        action). It strips the image from the last user message, then appends the
        assistant turn — so the **final** planner context matches the legacy path.

        Note the ordering nuance: the legacy ``_execute_step`` runs
        ``strip_last_image`` *before* ``execute``; the new path runs ``execute``
        first and strips here. This does not affect behavior because ADB execution
        only consumes the action dict, never the model context — so what matters
        is the *final* context (image stripped, assistant appended), which is
        equivalent. The oracle test asserts final-context equivalence, not strict
        step ordering.
        """
        if self._last_response is None:
            return
        self.planner.strip_last_image()
        self.planner.append_assistant(
            self._last_response.thinking, self._last_response.action
        )

    # -- dict -> Action -------------------------------------------------------

    def _map_action(self, d: dict[str, Any]) -> Action:
        metadata = d.get("_metadata")
        if metadata == "finish":
            return Action(
                type=ActionType.FINISH,
                text=d.get("message"),
                raw=d,
            )

        if metadata != "do":
            raise ValueError(f"Unknown action metadata: {metadata!r} in {d!r}")

        name = d.get("action")
        handler = _DICT_TO_ACTION.get(name)
        if handler is None:
            raise ValueError(f"Unknown action name: {name!r} in {d!r}")
        return handler(d)


def _coords(d: dict[str, Any], key: str = "element") -> Optional[list[float]]:
    val = d.get(key)
    return [float(c) for c in val] if val is not None else None


def _tap(d):
    return Action(type=ActionType.TAP, coordinates=_coords(d), raw=d)


def _double_tap(d):
    return Action(type=ActionType.DOUBLE_TAP, coordinates=_coords(d), raw=d)


def _long_press(d):
    return Action(type=ActionType.LONG_PRESS, coordinates=_coords(d), raw=d)


def _type_text(d):
    return Action(type=ActionType.TYPE_TEXT, text=d.get("text", ""), raw=d)


def _swipe(d):
    start = d.get("start") or []
    end = d.get("end") or []
    coords = [float(c) for c in (*start, *end)] if start and end else None
    return Action(type=ActionType.SWIPE, coordinates=coords, raw=d)


def _back(d):
    return Action(type=ActionType.BACK, raw=d)


def _home(d):
    return Action(type=ActionType.HOME, raw=d)


def _launch(d):
    return Action(type=ActionType.LAUNCH_APP, app=d.get("app"), raw=d)


def _wait(d):
    return Action(type=ActionType.WAIT, duration_ms=_parse_seconds(d.get("duration")), raw=d)


def _request_user(d):
    # Take_over carries the user-assist message; Interact has none, so synthesize.
    return Action(
        type=ActionType.REQUEST_USER,
        text=d.get("message", "User interaction required"),
        raw=d,
    )


def _noop(d):
    # Note/Call_API record or summarize the page; they do not change the env.
    return Action(type=ActionType.NOOP, raw=d)


def _parse_seconds(duration: Optional[str]) -> Optional[int]:
    """'3 seconds' -> 3000 ms. Tolerant of the 'x seconds' model format."""
    if duration is None:
        return None
    digits = "".join(ch for ch in str(duration) if ch.isdigit() or ch == ".")
    try:
        return int(float(digits) * 1000) if digits else None
    except ValueError:
        return None


# Dispatch table: legacy action name -> factory. Closed model action space.
_DICT_TO_ACTION: dict[str, Any] = {
    "Tap": _tap,
    "Double Tap": _double_tap,
    "Long Press": _long_press,
    "Type": _type_text,
    "Type_Name": _type_text,
    "Swipe": _swipe,
    "Back": _back,
    "Home": _home,
    "Launch": _launch,
    "Wait": _wait,
    "Take_over": _request_user,
    "Interact": _request_user,
    "Note": _noop,
    "Call_API": _noop,
}
