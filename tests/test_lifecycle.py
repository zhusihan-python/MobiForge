"""Gate B Step 2a.1: the adapter response lifecycle is wired through the Runner.

Locks the three review fixes:
  - P1#1 assistant append: after execution the Runner calls adapter.commit(),
    so the next model call sees the prior assistant turn (oracle-equivalent to
    the legacy plan->execute->append_assistant order).
  - P1#2 NOOP short-circuit: NOOP never reaches the backend; "no environment
    change" is runtime semantics.
  - P2#3 model_output: the raw model output flows into StepResult.model_output.
"""

import unittest
from datetime import datetime, timezone

from phone_agent.model.client import ModelResponse
from phone_agent.planner import ActionParser, ModelPlanner
from runtime import FreeformTask, InMemoryTrajectoryStore, Runner, RunStatus
from runtime.adapters import OpenAutoGLMAdapter
from runtime.boundary import EnvBackend, EnvResult
from runtime.schemas import Action, ActionType, Observation


class FakeModelClient:
    def __init__(self, script):
        # script: list of action strings, returned in order across requests.
        self._script = list(script)
        self._i = 0

    def request(self, context):
        action = self._script[self._i]
        self._i += 1
        return ModelResponse(thinking=f"think-{self._i}", action=action, raw_content=f"raw-{self._i}")


class CountingBackend(EnvBackend):
    """Records every execute() call and the actions it received."""

    def __init__(self):
        self.executed = []
        self._n = 0

    def reset(self):
        self._n = 0

    def observe(self):
        self._n += 1
        return Observation(
            id=f"obs-{self._n}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            screenshot="IMG",
            current_app="Settings",
        )

    def execute(self, action):
        self.executed.append(action)
        return EnvResult(success=True, message=None)


def _adapter(script):
    fake = FakeModelClient(script)
    planner = ModelPlanner(fake, "SYS", "cn", verbose=False)
    parser = ActionParser(verbose=False, lang="cn")
    return OpenAutoGLMAdapter(planner, parser)


class AssistantAppendLifecycleTests(unittest.TestCase):
    def test_commit_appends_assistant_turn_so_context_grows_correctly(self):
        """After two steps through the Runner, the planner context must contain
        the assistant turns (the legacy path always appended them). This fails
        if commit() is never called — context would be missing assistant msgs."""
        # step 1: tap ; step 2: finish
        adapter = _adapter(['do(action="Tap", element=[1,1])', 'finish(message="done")'])
        store = InMemoryTrajectoryStore()
        result = Runner(adapter, CountingBackend(), store=store).run(
            FreeformTask("打开设置", max_steps=5)
        )

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.steps_count, 2)
        # context after the run: system, user(1), assistant(1), user(2), assistant(2) = 5.
        # Without commit(), it would be only system+user+user = 3.
        ctx = adapter.planner.context_copy()
        self.assertEqual(len(ctx), 5)
        assistant_msgs = [m for m in ctx if m["role"] == "assistant"]
        self.assertEqual(len(assistant_msgs), 2)

    def test_commit_runs_after_execute_not_before(self):
        """The assistant turn is appended AFTER execution. Proved by having the
        backend observe the adapter's context during execute(): it must NOT yet
        contain the assistant turn for the current step."""
        adapter = _adapter(['do(action="Tap", element=[1,1])', 'finish(message="done")'])

        context_snapshot_during_execute = []

        class SnapshotBackend(CountingBackend):
            def execute(self, action):
                context_snapshot_during_execute.append(
                    len(adapter.planner.context_copy())
                )
                return super().execute(action)

        Runner(adapter, SnapshotBackend()).run(FreeformTask("t", max_steps=5))

        # During step 1's execute, context = system+user(1) = 2 (assistant not yet appended).
        self.assertEqual(context_snapshot_during_execute, [2])


class ModelOutputFlowTests(unittest.TestCase):
    def test_step_result_records_raw_model_output(self):
        adapter = _adapter(['finish(message="done")'])
        store = InMemoryTrajectoryStore()
        result = Runner(adapter, CountingBackend(), store=store).run(
            FreeformTask("t", max_steps=5)
        )

        self.assertEqual(result.steps_count, 1)
        # raw_content of the canned response is "raw-1".
        self.assertEqual(result.steps[0].model_output, "raw-1")
        self.assertEqual(store.steps[0].model_output, "raw-1")


class NoopShortCircuitTests(unittest.TestCase):
    def test_noop_does_not_call_backend_execute(self):
        """P1#2: NOOP must be short-circuited by the Runner — the backend's
        execute() is never called for it, yet the step records a success result."""
        adapter = _adapter(['do(action="Note", message="page looks fine")', 'finish(message="done")'])
        backend = CountingBackend()
        result = Runner(adapter, backend).run(FreeformTask("t", max_steps=5))

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.steps_count, 2)
        # The backend only executed the... nothing — Note is NOOP (not executed),
        # and finish is terminal (not executed). So execute() is NEVER called.
        self.assertEqual(backend.executed, [])
        # And the NOOP step still recorded a successful env_result.
        self.assertIsNotNone(result.steps[0].env_result)
        self.assertTrue(result.steps[0].env_result.success)
        self.assertEqual(result.steps[0].env_result.message, "noop")

    def test_noop_then_tap_only_executes_the_tap(self):
        adapter = _adapter([
            'do(action="Note", message="looking")',
            'do(action="Tap", element=[5,5])',
            'finish(message="done")',
        ])
        backend = CountingBackend()
        result = Runner(adapter, backend).run(FreeformTask("t", max_steps=5))

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        # Only the Tap reached the backend; the Note (NOOP) did not.
        self.assertEqual(len(backend.executed), 1)
        self.assertEqual(backend.executed[0].type, ActionType.TAP)


if __name__ == "__main__":
    unittest.main()
