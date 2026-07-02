"""Load task-suite fixtures from JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from runtime.schemas import (
    Action,
    ActionType,
    FreeformTask,
    JudgeRef,
    SafetyMeta,
    TaskSpec,
    VerifiableTask,
)
from runtime.task_suite import TaskCase


def load_task_case(path: str | Path) -> TaskCase:
    """Load one ``TaskCase`` from a JSON fixture file."""
    fixture_path = Path(path)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    return _parse_task_case(payload, source=fixture_path)


def load_task_cases(paths: Iterable[str | Path]) -> list[TaskCase]:
    """Load task cases from a sequence of files in deterministic path order."""
    return [load_task_case(path) for path in sorted(Path(p) for p in paths)]


def load_task_cases_from_dir(directory: str | Path, pattern: str = "*.json") -> list[TaskCase]:
    """Load all task-case fixtures matching ``pattern`` under ``directory``."""
    return load_task_cases(Path(directory).glob(pattern))


def _parse_task_case(payload: dict[str, Any], *, source: Path) -> TaskCase:
    case_id = payload.get("id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"{source}: fixture requires non-empty string id")

    task_payload = payload.get("task")
    if not isinstance(task_payload, dict):
        raise ValueError(f"{source}: fixture requires task object")

    metadata = _parse_metadata(payload.get("metadata") or {}, source=source)
    tags = payload.get("tags") or []
    if not isinstance(tags, list):
        raise ValueError(f"{source}: tags must be a list when present")

    return TaskCase(
        id=case_id,
        task=_parse_task(task_payload, source=source),
        tags=tuple(str(tag) for tag in tags),
        metadata=metadata,
    )


def _parse_task(payload: dict[str, Any], *, source: Path) -> TaskSpec:
    mode = payload.get("mode") or payload.get("type") or "verifiable"
    if mode == "freeform":
        return FreeformTask(
            description=_required_str(payload, "description", source),
            max_steps=_optional_int(payload.get("max_steps"), source, "max_steps"),
            timeout_seconds=_optional_float(
                payload.get("timeout_seconds"), source, "timeout_seconds"
            ),
        )
    if mode != "verifiable":
        raise ValueError(f"{source}: unknown task mode {mode!r}")

    setup_actions = payload.get("setup_actions")
    return VerifiableTask(
        description=_required_str(payload, "description", source),
        judge_ref=_parse_judge_ref(payload.get("judge_ref"), source=source),
        setup=payload.get("setup"),
        setup_actions=(
            [_parse_action(action, source=source) for action in setup_actions]
            if setup_actions is not None
            else None
        ),
        goal=payload.get("goal"),
        constraints=list(payload.get("constraints") or []),
        answer_schema=payload.get("answer_schema"),
        max_steps=_optional_int(payload.get("max_steps"), source, "max_steps"),
        timeout_seconds=_optional_float(
            payload.get("timeout_seconds"), source, "timeout_seconds"
        ),
    )


def _parse_judge_ref(payload: Any, *, source: Path) -> JudgeRef | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f"{source}: judge_ref must be an object")
    judge_type = payload.get("judge_type")
    if not isinstance(judge_type, str):
        raise ValueError(f"{source}: judge_ref.judge_type must be a string")
    return JudgeRef(
        judge_type=judge_type,
        entrypoint=payload.get("entrypoint"),
        config=payload.get("config"),
    )


def _parse_metadata(payload: dict[str, Any], *, source: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{source}: metadata must be an object when present")
    metadata = dict(payload)
    script = metadata.get("script")
    if script is not None:
        if not isinstance(script, list):
            raise ValueError(f"{source}: metadata.script must be a list")
        metadata["script"] = [_parse_action(action, source=source) for action in script]
    return metadata


def _parse_action(payload: Any, *, source: Path) -> Action:
    if not isinstance(payload, dict):
        raise ValueError(f"{source}: action entries must be objects")
    action_type = payload.get("type")
    if not isinstance(action_type, str):
        raise ValueError(f"{source}: action.type must be a string")
    safety = payload.get("safety")
    return Action(
        type=ActionType(action_type),
        coordinates=payload.get("coordinates"),
        text=payload.get("text"),
        app=payload.get("app"),
        duration_ms=payload.get("duration_ms"),
        raw=payload.get("raw"),
        safety=(
            SafetyMeta(
                reason=_required_str(safety, "reason", source),
                confirmed=bool(safety.get("confirmed", False)),
            )
            if isinstance(safety, dict)
            else None
        ),
    )


def _required_str(payload: dict[str, Any], key: str, source: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{source}: {key} must be a non-empty string")
    return value


def _optional_int(value: Any, source: Path, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"{source}: {field} must be an int when present")
    return value


def _optional_float(value: Any, source: Path, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"{source}: {field} must be a number when present")
    return float(value)
