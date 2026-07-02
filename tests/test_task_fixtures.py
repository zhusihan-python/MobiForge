"""JSON task fixture loading tests."""

import json
import tempfile
import unittest
from pathlib import Path

from runtime import (
    Action,
    ActionType,
    VerifiableTask,
    load_sim_smoke_cases,
    load_task_case,
    load_task_cases_from_dir,
)


class TaskFixtureLoadingTests(unittest.TestCase):
    def test_builtin_sim_fixtures_load_as_task_cases(self):
        cases = load_sim_smoke_cases()

        self.assertEqual(
            [case.id for case in cases],
            ["sim.open_settings", "sim.type_note", "sim.back_navigation"],
        )
        self.assertTrue(all(isinstance(case.task, VerifiableTask) for case in cases))
        self.assertTrue(all(case.task.judge_ref is not None for case in cases))
        self.assertTrue(all(isinstance(case.metadata["script"][0], Action) for case in cases))
        self.assertEqual(cases[0].metadata["script"][0].type, ActionType.LAUNCH_APP)

    def test_load_task_case_from_json_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "case.json"
            path.write_text(
                json.dumps(
                    {
                        "id": "sim.fixture",
                        "tags": ["sim"],
                        "task": {
                            "mode": "verifiable",
                            "description": "fixture task",
                            "judge_ref": {
                                "judge_type": "state",
                                "config": {
                                    "expected_state": {"current_app": "Settings"}
                                },
                            },
                            "setup": {"current_app": "home"},
                            "max_steps": 3,
                        },
                        "metadata": {
                            "script": [
                                {"type": "launch_app", "app": "Settings"},
                                {"type": "finish", "text": "done"},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            case = load_task_case(path)

        self.assertEqual(case.id, "sim.fixture")
        self.assertEqual(case.tags, ("sim",))
        self.assertEqual(case.task.description, "fixture task")
        self.assertEqual(case.task.setup["current_app"], "home")
        self.assertEqual(case.metadata["script"][0].app, "Settings")
        self.assertEqual(case.metadata["script"][1].type, ActionType.FINISH)

    def test_load_task_cases_from_dir_sorts_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            for filename, case_id in [("b.json", "case.b"), ("a.json", "case.a")]:
                (directory / filename).write_text(
                    json.dumps(
                        {
                            "id": case_id,
                            "task": {
                                "mode": "verifiable",
                                "description": case_id,
                                "max_steps": 1,
                            },
                        }
                    ),
                    encoding="utf-8",
                )

            cases = load_task_cases_from_dir(directory)

        self.assertEqual([case.id for case in cases], ["case.a", "case.b"])

    def test_invalid_fixture_reports_source_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.json"
            path.write_text(json.dumps({"task": {}}), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                load_task_case(path)

        self.assertIn("bad.json", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
