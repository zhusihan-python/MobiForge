"""Task-suite batch runner tests."""

import json
import tempfile
import unittest
from pathlib import Path

from runtime import (
    Action,
    ActionType,
    JudgeRef,
    RunStatus,
    TaskCase,
    VerifiableTask,
    load_sim_smoke_cases,
    make_sim_smoke_cases,
    run_sim_smoke_suite,
)


class SimSmokeSuiteTests(unittest.TestCase):
    def test_builtin_sim_smoke_suite_passes_and_summarizes(self):
        result = run_sim_smoke_suite()

        self.assertEqual(result.total, 6)
        self.assertEqual(result.passed, 6)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.success_rate, 1.0)
        self.assertGreaterEqual(result.average_steps, 2.0)
        self.assertEqual(
            [case.case_id for case in result.cases],
            [
                "sim.open_settings",
                "sim.type_note",
                "sim.back_navigation",
                "sim.browser_search",
                "sim.form_fill",
                "sim.multi_step_navigation",
            ],
        )

    def test_failed_case_is_reported_with_judge_reason(self):
        bad_case = TaskCase(
            id="sim.bad_expected_app",
            task=VerifiableTask(
                description="open wrong app",
                judge_ref=JudgeRef(
                    judge_type="state",
                    config={"expected_state": {"current_app": "Settings"}},
                ),
                setup={"current_app": "home"},
                max_steps=5,
            ),
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
        self.assertTrue(all(case.judge is None for case in cases))
        self.assertTrue(all(case.task.judge_ref is not None for case in cases))

    def test_suite_report_dict_is_json_serializable(self):
        result = run_sim_smoke_suite()

        report = result.to_report_dict(generated_at="2026-07-02T00:00:00+00:00")

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["generated_at"], "2026-07-02T00:00:00+00:00")
        self.assertEqual(report["summary"]["total"], 6)
        self.assertEqual(report["summary"]["passed"], 6)
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
        self.assertEqual(len(written["cases"]), 6)

    def test_diagnostic_fixture_can_be_run_explicitly(self):
        diagnostics_dir = (
            Path(__file__).resolve().parents[1] / "tasks" / "sim" / "diagnostics"
        )
        result = run_sim_smoke_suite(load_sim_smoke_cases(diagnostics_dir))

        self.assertEqual(result.total, 1)
        self.assertEqual(result.passed, 0)
        self.assertEqual(result.failed, 1)
        self.assertEqual(
            result.failed_cases[0].case_id,
            "sim.diagnostics.wrong_expected_app",
        )
        self.assertIn("current_app", result.failed_cases[0].run_result.final_judge.reason)


if __name__ == "__main__":
    unittest.main()
