import base64
import json
import tempfile
import unittest
from types import SimpleNamespace

from runtime import RunRecorder, TaskRunner, TaskStatus


PNG_1X1 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
).decode("ascii")


def make_step(*, success=True, finished=True, message="done"):
    action = (
        {"_metadata": "finish", "message": message}
        if finished
        else {"_metadata": "do", "action": "Tap", "element": [500, 500]}
    )
    return SimpleNamespace(
        success=success,
        finished=finished,
        action=action,
        thinking="finished",
        message=message,
        screenshot_base64=PNG_1X1,
        screenshot_width=1,
        screenshot_height=1,
        screenshot_is_sensitive=False,
        current_app="Settings",
        model_output=f"finish(message={message!r})",
        duration_ms=12,
    )


class FakeAgent:
    def __init__(self, steps, max_steps=3):
        self.steps = iter(steps)
        self.agent_config = SimpleNamespace(max_steps=max_steps)
        self.reset_count = 0
        self.tasks = []

    def reset(self):
        self.reset_count += 1

    def step(self, task=None):
        self.tasks.append(task)
        return next(self.steps)


class RuntimeTests(unittest.TestCase):
    def test_successful_run_writes_complete_trace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = RunRecorder(temp_dir)
            agent = FakeAgent(
                [
                    make_step(finished=False, message=None),
                    make_step(message="opened settings"),
                ]
            )

            result = TaskRunner(
                agent,
                recorder,
                metadata={"device_id": "emulator-5554"},
            ).run("打开设置")

            self.assertEqual(result.status, TaskStatus.SUCCEEDED)
            self.assertEqual(result.steps, 2)
            self.assertEqual(agent.tasks, ["打开设置", None])
            self.assertTrue((result.run_dir / "screenshots/001.png").exists())
            self.assertTrue((result.run_dir / "screenshots/002.png").exists())

            lines = (result.run_dir / "steps.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["action"]["_metadata"], "do")

            final = json.loads(
                (result.run_dir / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(final["status"], "SUCCEEDED")

    def test_failed_step_marks_run_failed(self):
        agent = FakeAgent([make_step(success=False, message="model error")])

        result = TaskRunner(agent).run("fail")

        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.message, "model error")

    def test_timeout_is_checked_before_first_step(self):
        agent = FakeAgent([make_step()])

        result = TaskRunner(agent, timeout_seconds=0).run("too slow")

        self.assertEqual(result.status, TaskStatus.TIMEOUT)
        self.assertEqual(result.steps, 0)
        self.assertEqual(agent.tasks, [])


if __name__ == "__main__":
    unittest.main()
