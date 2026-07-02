"""Task-suite batch runner tests."""

import json
import tempfile
import unittest

from runtime import (
    Action,
    ActionType,
    RunStatus,
    StateJudge,
    TaskCase,
    VerifiableTask,
    make_sim_smoke_cases,
    run_sim_smoke_suite,
)


class SimSmokeSuiteTests(unittest.TestCase):
    def test_builtin_sim_smoke_suite_passes_and_summarizes(self):
        result = run_sim_smoke_suite()

        self.assertEqual(result.total, 3)
        self.assertEqual(result.passed, 3)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.success_rate, 1.0)
        self.assertGreaterEqual(result.average_steps, 2.0)
        self.assertEqual([case.case_id for case in result.cases], [
            "sim.open_settings",
            "sim.type_note",
            "sim.back_navigation",
        ])

    def test_failed_case_is_reported_with_judge_reason(self):
        bad_case = TaskCase(
            id="sim.bad_expected_app",
            task=VerifiableTask(
                description="open wrong app",
                setup={"current_app": "home"},
                max_steps=5,
            ),
            judge=StateJudge({"current_app": "Settings"}),
            metadata={
                "script": [
                    Action(type=ActionType.LAUNCH_APP, app="Browser"),
                    Action(type=ActionType.FINISH, text="done"),
                ]
            },
        )

        result = run_sim_smoke_suite([bad_case])

        self.assertEqual(result.total, 1)
        self.assertEqual(result.passed, 0)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.success_rate, 0.0)
        self.assertEqual(len(result.failed_cases), 1)
        failed = result.failed_cases[0]
        self.assertEqual(failed.case_id, "sim.bad_expected_app")
        self.assertEqual(failed.status, RunStatus.FAILED)
        self.assertIn("current_app", failed.run_result.final_judge.reason)

    def test_task_cases_are_regular_runtime_specs(self):
        cases = make_sim_smoke_cases()

        self.assertTrue(all(isinstance(case.task, VerifiableTask) for case in cases))
        self.assertTrue(all(case.task.max_steps is not None for case in cases))
        self.assertTrue(all(case.judge is not None for case in cases))

    def test_suite_report_dict_is_json_serializable(self):
        result = run_sim_smoke_suite()

        report = result.to_report_dict(generated_at="2026-07-02T00:00:00+00:00")

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["generated_at"], "2026-07-02T00:00:00+00:00")
        self.assertEqual(report["summary"]["total"], 3)
        self.assertEqual(report["summary"]["passed"], 3)
        self.assertEqual(report["summary"]["failed"], 0)
        first = report["cases"][0]
        self.assertEqual(first["case_id"], "sim.open_settings")
        self.assertEqual(first["status"], "succeeded")
        self.assertTrue(first["final_judge"]["passed"])
        self.assertEqual(first["last_observation"]["current_app"], "Settings")

        # Proves there are no enums/dataclasses left in the report payload.
        json.dumps(report, ensure_ascii=False)

    def test_suite_report_json_and_write_json(self):
        result = run_sim_smoke_suite()

        json_text = result.to_json(
            generated_at="2026-07-02T00:00:00+00:00",
            indent=2,
        )
        parsed = json.loads(json_text)
        self.assertEqual(parsed["summary"]["success_rate"], 1.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = result.write_json(
                f"{temp_dir}/sim-report.json",
                generated_at="2026-07-02T00:00:00+00:00",
            )
            written = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(written["generated_at"], "2026-07-02T00:00:00+00:00")
        self.assertEqual(len(written["cases"]), 3)


if __name__ == "__main__":
    unittest.main()
