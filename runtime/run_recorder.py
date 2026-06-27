"""Persist task metadata, screenshots, step events, and final results."""

import base64
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunRecorder:
    """Write one task run to a self-contained directory."""

    def __init__(self, runs_dir: str | Path = "runs"):
        self.runs_dir = Path(runs_dir)
        self.run_dir: Path | None = None
        self.screenshots_dir: Path | None = None

    def start(self, task: str, metadata: dict[str, Any] | None = None) -> Path:
        """Create the run directory and write initial metadata."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        run_id = uuid.uuid4().hex[:8]
        slug = self._slugify(task)
        self.run_dir = self.runs_dir / f"{timestamp}_{run_id}_{slug}"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=False)

        meta = {
            "run_id": run_id,
            "task": task,
            "status": "RUNNING",
            "created_at": self._now(),
            **(metadata or {}),
        }
        self._write_json(self.run_dir / "meta.json", meta)
        return self.run_dir

    def record_step(self, step: int, step_result: Any) -> dict[str, Any]:
        """Append one agent step and save its pre-action screenshot."""
        self._ensure_started()
        screenshot_path = None
        screenshot_base64 = getattr(step_result, "screenshot_base64", None)
        if screenshot_base64:
            screenshot_path = f"screenshots/{step:03d}.png"
            image_bytes = base64.b64decode(screenshot_base64)
            (self.run_dir / screenshot_path).write_bytes(image_bytes)

        record = {
            "step": step,
            "timestamp": self._now(),
            "screenshot": screenshot_path,
            "screen": {
                "width": getattr(step_result, "screenshot_width", None),
                "height": getattr(step_result, "screenshot_height", None),
                "is_sensitive": getattr(
                    step_result, "screenshot_is_sensitive", False
                ),
                "current_app": getattr(step_result, "current_app", None),
            },
            "model_output": getattr(step_result, "model_output", None),
            "thinking": getattr(step_result, "thinking", ""),
            "action": getattr(step_result, "action", None),
            "duration_ms": getattr(step_result, "duration_ms", None),
            "status": "ok" if getattr(step_result, "success", False) else "error",
            "finished": getattr(step_result, "finished", False),
            "message": getattr(step_result, "message", None),
        }
        with (self.run_dir / "steps.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def finish(self, result: dict[str, Any]) -> None:
        """Write the final result and update the run metadata status."""
        self._ensure_started()
        final_result = {"finished_at": self._now(), **result}
        self._write_json(self.run_dir / "result.json", final_result)

        meta_path = self.run_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = result["status"]
        meta["finished_at"] = final_result["finished_at"]
        self._write_json(meta_path, meta)

    def _ensure_started(self) -> None:
        if self.run_dir is None:
            raise RuntimeError("RunRecorder.start() must be called first")

    @staticmethod
    def _slugify(task: str) -> str:
        slug = re.sub(r"[^\w-]+", "_", task.strip(), flags=re.UNICODE).strip("_")
        return (slug[:48] or "task").lower()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
