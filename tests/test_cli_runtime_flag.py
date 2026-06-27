"""Phase 2 Batch 1: the --runtime flag plumbs the new boundary path into the CLI.

Default is legacy; `--runtime new` routes through the boundary-aware Runner +
OpenAutoGLMAdapter + AdbDeviceEnv, ADB-only (hdc/ios rejected). No device, no
model server: tests exercise the pure builder + dispatch, with the legacy/new
run functions mocked.
"""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from phone_agent.agent import AgentConfig
from phone_agent.device_factory import DeviceType
from phone_agent.model import ModelConfig
from runtime import Runner
from runtime.adapters import OpenAutoGLMAdapter
from runtime.env_backends import AdbDeviceEnv

import main as main_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _model_config():
    return ModelConfig(model_name="autoglm-phone-9b")


def _agent_config(max_steps=20):
    # Build with a real lang so __post_init__ resolves system_prompt.
    return AgentConfig(max_steps=max_steps, lang="cn", verbose=False)


def _args(runtime="legacy", runs_dir="runs", no_record=False, timeout=None, device_type="adb"):
    return SimpleNamespace(
        runtime=runtime,
        runs_dir=runs_dir,
        no_record=no_record,
        timeout=timeout,
        device_type=device_type,
    )


# ---------------------------------------------------------------------------
# 1. _build_new_runner wiring + timeout/max_steps propagation
# ---------------------------------------------------------------------------


class BuildNewRunnerTests(unittest.TestCase):
    def test_returns_runner_with_adapter_and_adb_backend(self):
        runner = main_mod._build_new_runner(
            _model_config(), _agent_config(max_steps=15), device_id=None, timeout_seconds=12.5
        )
        self.assertIsInstance(runner, Runner)
        self.assertIsInstance(runner.adapter, OpenAutoGLMAdapter)
        self.assertIsInstance(runner.backend, AdbDeviceEnv)

    def test_timeout_and_max_steps_propagate_into_runner(self):
        runner = main_mod._build_new_runner(
            _model_config(), _agent_config(max_steps=42), device_id="dev1", timeout_seconds=7.0
        )
        self.assertEqual(runner.max_steps, 42)
        self.assertEqual(runner.timeout_seconds, 7.0)


# ---------------------------------------------------------------------------
# 2-5. _select_and_run dispatch (no device: legacy/new mocked)
# ---------------------------------------------------------------------------


class DispatchTests(unittest.TestCase):
    def _select(self, runtime, device_type):
        args = _args(runtime=runtime, device_type=device_type)
        with patch.object(main_mod, "_run_task_legacy", return_value="LEGACY") as legacy, \
             patch.object(main_mod, "_run_task_new", return_value="NEW") as new:
            result = main_mod._select_and_run(
                "task", args, _coerce_device_type(device_type),
                agent=object(), agent_config=_agent_config(), model_config=_model_config(),
            )
        return result, legacy.called, new.called

    def test_default_runtime_is_legacy(self):
        result, legacy_called, new_called = self._select("legacy", "adb")
        self.assertTrue(legacy_called)
        self.assertFalse(new_called)
        self.assertEqual(result, "LEGACY")

    def test_new_runtime_on_adb_routes_to_new(self):
        result, legacy_called, new_called = self._select("new", "adb")
        self.assertFalse(legacy_called)
        self.assertTrue(new_called)
        self.assertEqual(result, "NEW")

    def test_new_runtime_on_ios_rejected_returns_none(self):
        result, legacy_called, new_called = self._select("new", "ios")
        self.assertIsNone(result)
        self.assertFalse(legacy_called)
        self.assertFalse(new_called)

    def test_new_runtime_on_hdc_rejected_returns_none(self):
        result, legacy_called, new_called = self._select("new", "hdc")
        self.assertIsNone(result)
        self.assertFalse(legacy_called)
        self.assertFalse(new_called)

    def test_rejection_prints_clear_notice(self):
        args = _args(runtime="new", device_type="ios")
        buf = io.StringIO()
        with redirect_stdout(buf), \
             patch.object(main_mod, "_run_task_legacy") as legacy, \
             patch.object(main_mod, "_run_task_new") as new:
            main_mod._select_and_run(
                "task", args, DeviceType.IOS, object(), _agent_config(), _model_config()
            )
        out = buf.getvalue()
        self.assertIn("ADB only", out)
        self.assertFalse(legacy.called)
        self.assertFalse(new.called)


def _coerce_device_type(name):
    return {"adb": DeviceType.ADB, "hdc": DeviceType.HDC, "ios": DeviceType.IOS}[name]


# ---------------------------------------------------------------------------
# 6. legacy output unchanged (incl. Trace:)
# ---------------------------------------------------------------------------


class LegacyOutputTests(unittest.TestCase):
    def test_legacy_prints_trace_line_and_constructs_taskrunner(self):
        """The legacy branch must keep its RunRecorder + TaskRunner + Trace: output,
        byte-for-byte. Mock TaskRunner.run to a scripted outcome with run_dir set."""
        from runtime.task_runner import TaskRunResult, TaskStatus

        outcome = TaskRunResult(
            status=TaskStatus.SUCCEEDED,
            message="done",
            steps=3,
            duration_ms=1234,
            run_dir="runs/xyz",
        )
        args = _args(runtime="legacy", runs_dir="runs")
        buf = io.StringIO()
        with redirect_stdout(buf), \
             patch.object(main_mod, "TaskRunner") as TK:
            TK.return_value.run.return_value = outcome
            main_mod._run_task_legacy("task", args, agent=object(),
                                      agent_config=_agent_config(), model_config=_model_config())

        out = buf.getvalue()
        self.assertIn("Result: done", out)
        # Legacy uses the spike's uppercase TaskStatus values (distinct from the
        # new path's lowercase RunStatus) — this is the byte-for-byte legacy form.
        self.assertIn("Status: SUCCEEDED", out)
        self.assertIn("Steps: 3", out)
        self.assertIn("Trace: runs/xyz", out)  # legacy keeps the Trace: line
        # TaskRunner was constructed (with recorder + metadata), not bypassed.
        self.assertTrue(TK.called)


# ---------------------------------------------------------------------------
# 7. new path has no Trace: line
# ---------------------------------------------------------------------------


class NewOutputTests(unittest.TestCase):
    def test_new_path_output_has_no_trace_line(self):
        from runtime.schemas import RunStatus
        from runtime.boundary import RunResult

        result = RunResult(
            status=RunStatus.SUCCEEDED, message="done", steps_count=2,
            duration_ms=500, steps=[],
        )
        args = _args(runtime="new")
        buf = io.StringIO()
        with redirect_stdout(buf), \
             patch.object(main_mod, "_build_new_runner") as builder:
            builder.return_value.run.return_value = result
            main_mod._run_task_new("task", args, _model_config(), _agent_config())

        out = buf.getvalue()
        self.assertIn("Result: done", out)
        self.assertIn("Status: succeeded", out)
        self.assertIn("Steps: 2", out)  # new path uses steps_count
        self.assertNotIn("Trace:", out)  # new path: no disk store, no Trace:


if __name__ == "__main__":
    unittest.main()
