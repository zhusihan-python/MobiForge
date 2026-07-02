"""DiskTrajectoryStore writes normalized Runner trajectories."""

import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone

from runtime import (
    Action,
    ActionType,
    DiskTrajectoryStore,
    EnvResult,
    FreeformTask,
    JudgeResult,
    Observation,
    RunStatus,
    Size,
    StepResult,
)


PNG_1X1 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
).decode("ascii")


def _step():
    return StepResult(
        step=1,
        observation=Observation(
            id="obs-1",
            timestamp=datetime.now(timezone.utc).isoformat(),
            screenshot=PNG_1X1,
            screenshot_size=Size(width=1, height=1),
            current_app="Settings",
            env_state={"current_app": "Settings"},
        ),
        action=Action(type=ActionType.TAP, coordinates=[500, 500]),
        model_output='do(action="Tap", element=[500,500])',
        env_result=EnvResult(success=True),
        judge_result=JudgeResult(
            passed=False,
            reason="not terminal yet",
            judge_type="state",
        ),
        duration_ms=12,
    )


class DiskTrajectoryStoreTests(unittest.TestCase):
    def test_writes_meta_steps_screenshot_and_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DiskTrajectoryStore(
                temp_dir,
                metadata={"runtime": "new", "model": "fake-model"},
            )
            task = FreeformTask("open settings", max_steps=5)

            store.start(
                "abc123",
                task,
                backend_meta={"backend": "FakeBackend"},
                agent_meta={"adapter": "FakeAdapter"},
            )
            store.record_step(_step())
            store.finish(
                RunStatus.SUCCEEDED,
                JudgeResult(passed=True, reason="done", judge_type="state"),
            )

            self.assertIsNotNone(store.run_dir)
            self.assertTrue((store.run_dir / "meta.json").exists())
            self.assertTrue((store.run_dir / "result.json").exists())
            self.assertTrue((store.run_dir / "screenshots/001.png").exists())

            meta = json.loads((store.run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["run_id"], "abc123")
            self.assertEqual(meta["status"], "succeeded")
            self.assertEqual(meta["runtime"], "new")
            self.assertEqual(meta["task"]["description"], "open settings")

            lines = (store.run_dir / "steps.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(lines), 1)
            step = json.loads(lines[0])
            self.assertEqual(step["action"]["type"], "tap")
            self.assertEqual(step["env_result"]["success"], True)
            self.assertEqual(step["observation"]["screenshot"], "screenshots/001.png")
            self.assertEqual(step["observation"]["env_state"]["current_app"], "Settings")

            result = json.loads(
                (store.run_dir / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(result["final_judge"]["passed"])


if __name__ == "__main__":
    unittest.main()
