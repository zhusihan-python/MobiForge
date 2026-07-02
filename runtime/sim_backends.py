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
    - ``current_page``: page/screen within the current app.
    - ``history``: app navigation stack used by ``BACK``.
    - ``page_history``: in-app navigation stack used by ``BACK`` before app
      history.
    - ``text_input``: latest text typed by ``TYPE_TEXT``.
    - ``focused_field``: dotted state path populated by the next ``TYPE_TEXT``.
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
            "current_page": "home",
            "history": [],
            "page_history": [],
            "focused_field": None,
            "text_input": "",
            "browser": {
                "query": "",
                "results_visible": False,
                "submitted": False,
            },
            "form": {
                "fields": {},
                "submitted": False,
            },
            "navigation": {
                "visited": [],
            },
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
        if action.type in {
            ActionType.TAP,
            ActionType.DOUBLE_TAP,
            ActionType.LONG_PRESS,
        }:
            return self._tap(action)

        self._record_event(action)
        return EnvResult(success=True)

    def _launch_app(self, action: Action) -> EnvResult:
        if not action.app:
            return EnvResult(success=False, message="No app specified")
        self._push_history()
        self.state["current_app"] = action.app
        self.state["current_page"] = self._default_page_for(action.app)
        self.state["page_history"] = []
        self.state["focused_field"] = None
        self._record_event(action)
        return EnvResult(success=True)

    def _home(self, action: Action) -> EnvResult:
        self._push_history()
        self.state["current_app"] = "home"
        self.state["current_page"] = "home"
        self.state["page_history"] = []
        self.state["focused_field"] = None
        self._record_event(action)
        return EnvResult(success=True)

    def _back(self, action: Action) -> EnvResult:
        page_history = self.state.setdefault("page_history", [])
        if page_history:
            self.state["current_page"] = page_history.pop()
            self._mark_page_visited(self.state["current_page"])
        else:
            history = self.state.setdefault("history", [])
            self.state["current_app"] = history.pop() if history else "home"
            self.state["current_page"] = self._default_page_for(
                str(self.state.get("current_app", "home"))
            )
        self.state["focused_field"] = None
        self._record_event(action)
        return EnvResult(success=True)

    def _type_text(self, action: Action) -> EnvResult:
        text = action.text or ""
        self.state["text_input"] = text
        focused_field = self.state.get("focused_field")
        if isinstance(focused_field, str) and focused_field:
            _set_dotted_path(self.state, focused_field, text)
        elif self.state.get("current_app") == "Browser":
            self.state.setdefault("browser", {})["query"] = text
        self._record_event(action)
        return EnvResult(success=True)

    def _tap(self, action: Action) -> EnvResult:
        target = _action_hint(action, "target")
        if target in {"search_box", "browser.search_box"}:
            self.state["focused_field"] = "browser.query"
        elif target in {"search_submit", "browser.search_submit"}:
            browser = self.state.setdefault("browser", {})
            if not browser.get("query"):
                browser["query"] = self.state.get("text_input", "")
            browser["submitted"] = True
            browser["results_visible"] = True
            self._navigate_page("results")
            self.state["focused_field"] = None
        elif isinstance(target, str) and target.startswith("field:"):
            field_name = target.split(":", 1)[1]
            if not field_name:
                return EnvResult(success=False, message="Empty form field target")
            self.state["focused_field"] = f"form.fields.{field_name}"
        elif target == "submit_form":
            form = self.state.setdefault("form", {})
            fields = form.setdefault("fields", {})
            form["submitted"] = True
            form["last_submission"] = deepcopy(fields)
            self._navigate_page("submitted")
            self.state["focused_field"] = None
        elif isinstance(target, str) and target.startswith("nav:"):
            page = target.split(":", 1)[1]
            if not page:
                return EnvResult(success=False, message="Empty navigation target")
            self._navigate_page(page)
            self.state["focused_field"] = None
        elif _action_hint(action, "page"):
            self._navigate_page(str(_action_hint(action, "page")))
            self.state["focused_field"] = None

        self._record_event(action)
        return EnvResult(success=True)

    def _push_history(self) -> None:
        current = self.state.get("current_app")
        if current:
            self.state.setdefault("history", []).append(current)

    def _default_page_for(self, app: str) -> str:
        app_pages = self.state.get("app_pages")
        if isinstance(app_pages, dict) and isinstance(app_pages.get(app), str):
            return app_pages[app]
        return "home"

    def _navigate_page(self, page: str) -> None:
        current_page = self.state.get("current_page")
        if current_page and current_page != page:
            self.state.setdefault("page_history", []).append(current_page)
        self.state["current_page"] = page
        self._mark_page_visited(page)

    def _mark_page_visited(self, page: str) -> None:
        navigation = self.state.setdefault("navigation", {})
        navigation["last_page"] = page
        navigation.setdefault("visited", []).append(page)

    def _record_event(self, action: Action) -> None:
        event = {
            "type": action.type.value,
            "coordinates": list(action.coordinates) if action.coordinates else None,
            "text": action.text,
            "app": action.app,
            "duration_ms": action.duration_ms,
            "target": _action_hint(action, "target"),
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


def _action_hint(action: Action, key: str) -> Any:
    raw = action.raw if isinstance(action.raw, dict) else {}
    sim = raw.get("sim") if isinstance(raw.get("sim"), dict) else {}
    return sim.get(key, raw.get(key))


def _set_dotted_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in path.split(".") if part]
    if not parts:
        return
    cursor = target
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value
