"""Disk-backed trajectory store for the normalized runtime."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from runtime.boundary import TrajectoryStore
from runtime.schemas import (
    JudgeResult,
    ResumePoint,
    RunStatus,
    StepResult,
    TaskSpec,
)


class DiskTrajectoryStore(TrajectoryStore):
    """Persist normalized ``Runner`` trajectories to disk.

    Layout intentionally mirrors the legacy ``RunRecorder`` directory shape:
    ``meta.json``, ``steps.jsonl``, ``screenshots/``, and ``result.json``. The
    payload is normalized ``StepResult`` data, not legacy ``PhoneAgent`` fields.
    """

    def __init__(
        self,
        runs_dir: str | Path = "runs",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.runs_dir = Path(runs_dir)
        self.metadata = dict(metadata or {})
        self.run_dir: Optional[Path] = None
        self.screenshots_dir: Optional[Path] = None
        self.run_id: Optional[str] = None

    def start(
        self,
        run_id: str,
        task: TaskSpec,
        backend_meta: dict[str, Any],
        agent_meta: dict[str, Any],
    ) -> None:
        self.run_id = run_id
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        slug = _slugify(_task_description(task))
        self.run_dir = self.runs_dir / f"{timestamp}_{run_id}_{slug}"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=False)

        meta = {
            "schema_version": 1,
            "run_id": run_id,
            "task": _to_jsonable(task),
            "task_description": _task_description(task),
            "status": "running",
            "created_at": _now(),
            "backend": dict(backend_meta),
            "agent": dict(agent_meta),
            **self.metadata,
        }
        _write_json(self.run_dir / "meta.json", meta)

    def record_step(self, step: StepResult) -> None:
        self._ensure_started()
        observation = step.observation
        screenshot_path = self._save_screenshot(step.step, observation.screenshot)
        observation_payload = _to_jsonable(observation)
        if screenshot_path is not None:
            observation_payload["screenshot"] = screenshot_path

        record = {
            "step": step.step,
            "timestamp": observation.timestamp,
            "screenshot": screenshot_path,
            "observation": observation_payload,
            "model_output": step.model_output,
            "action": _to_jsonable(step.action),
            "env_result": _to_jsonable(step.env_result),
            "judge_result": _to_jsonable(step.judge_result),
            "status": step.status,
            "error": _to_jsonable(step.error),
            "resume_point": _to_jsonable(step.resume_point),
            "duration_ms": step.duration_ms,
        }
        with (self.run_dir / "steps.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def finish(
        self,
        status: RunStatus,
        final_judge: Optional[JudgeResult] = None,
        resume_point: Optional[ResumePoint] = None,
    ) -> None:
        self._ensure_started()
        final_result = {
            "finished_at": _now(),
            "status": status.value,
            "final_judge": _to_jsonable(final_judge),
            "resume_point": _to_jsonable(resume_point),
        }
        _write_json(self.run_dir / "result.json", final_result)

        meta_path = self.run_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = status.value
        meta["finished_at"] = final_result["finished_at"]
        _write_json(meta_path, meta)

    def _save_screenshot(self, step: int, screenshot: Optional[str]) -> Optional[str]:
        if not screenshot:
            return None

        data = screenshot
        if data.startswith("data:image/"):
            _, _, data = data.partition(",")

        try:
            image_bytes = base64.b64decode(data.strip(), validate=True)
        except (binascii.Error, ValueError):
            return screenshot

        screenshot_path = f"screenshots/{step:03d}.png"
        (self.run_dir / screenshot_path).write_bytes(image_bytes)
        return screenshot_path

    def _ensure_started(self) -> None:
        if self.run_dir is None or self.screenshots_dir is None:
            raise RuntimeError("DiskTrajectoryStore.start() must be called first")


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _task_description(task: TaskSpec) -> str:
    return getattr(task, "description", str(task))


def _slugify(task: str) -> str:
    slug = re.sub(r"[^\w-]+", "_", task.strip(), flags=re.UNICODE).strip("_")
    return (slug[:48] or "task").lower()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
