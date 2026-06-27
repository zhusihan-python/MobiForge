"""Phase 1 Gate A: the boundary is backend-agnostic.

The central assertion is that one fake agent, run through the same ``Runner``
against two structurally different fake backends (one ADB-like with no
structured state, one sim-like with structured state), produces *identical
trajectory semantics* — same step count, same status, same action sequence,
same StepResult field set — differing only where the backends genuinely differ
(``observation.env_state``). When that holds, the schemas freeze at v1.

Style mirrors ``tests/test_runtime.py``: stdlib ``unittest``, no extra deps.
"""

import unittest
from datetime import datetime, timezone

from runtime import (
    Action,
    ActionType,
    EnvBackend,
    EnvResult,
    FreeformTask,
    InMemoryTrajectoryStore,
    Judge,
    JudgeResult,
    NoneJudge,
    Observation,
    Runner,
    RunStatus,
    VerifiableTask,
)
from runtime.boundary import AgentAdapter


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _obs(obs_id: str, *, env_state=None, current_app="Settings") -> Observation:
    return Observation(
        id=obs_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        current_app=current_app,
        env_state=env_state,
    )


class ScriptedAgent(AgentAdapter):
    """Replays a fixed action list regardless of backend — the equivalence probe."""

    def __init__(self, actions):
        self._actions = list(actions)
        self._iter = None
        self.received_observations = []

    def reset(self, task):
        self._iter = iter(self._actions)
        self.received_observations = []

    def act(self, observation):
        self.received_observations.append(observation)
        return next(self._iter)


class AdbLikeBackend(EnvBackend):
    """Real-device shape: screenshot + current_app, NO structured env_state."""

    def __init__(self):
        self.executed = []
        self._n = 0

    def reset(self):
        self._n = 0

    def observe(self):
        self._n += 1
        # env_state intentionally None: real ADB cannot read app state.
        return _obs(f"adb-{self._n}", env_state=None, current_app="Settings")

    def execute(self, action):
        self.executed.append(action)
        return EnvResult(success=True)


class SimLikeBackend(EnvBackend):
    """Simulation shape: carries structured env_state (MobileGym-style)."""

    def __init__(self):
        self.executed = []
        self._n = 0

    def reset(self):
        self._n = 0

    def observe(self):
        self._n += 1
        return _obs(
            f"sim-{self._n}",
            env_state={"settings_open": True, "app": "settings"},
            current_app="Settings",
        )

    def execute(self, action):
        self.executed.append(action)
        return EnvResult(success=True)


class FailingBackend(EnvBackend):
    """Backend whose execute() always fails — for error propagation."""

    def reset(self):
        pass

    def observe(self):
        return _obs("fail-1")

    def execute(self, action):
        return EnvResult(success=False, message="device rejected input")


def _tap(x=500, y=500):
    return Action(type=ActionType.TAP, coordinates=[x, y])


def _finish(msg="done"):
    return Action(type=ActionType.FINISH, text=msg)


def _request_user(question="which account?"):
    return Action(type=ActionType.REQUEST_USER, text=question)


# ---------------------------------------------------------------------------
# Gate A — the equivalence proof
# ---------------------------------------------------------------------------


class GateAEquivalenceTests(unittest.TestCase):
    """One fake agent, two structurally different backends, identical semantics."""

    def _run_both(self, actions):
        agent_actions = actions  # identical for both runs

        def make_task():
            return FreeformTask(description="打开设置", max_steps=10)

        adb_store, sim_store = InMemoryTrajectoryStore(), InMemoryTrajectoryStore()
        adb_result = Runner(
            ScriptedAgent(agent_actions), AdbLikeBackend(), store=adb_store
        ).run(make_task())
        sim_result = Runner(
            ScriptedAgent(agent_actions), SimLikeBackend(), store=sim_store
        ).run(make_task())
        return adb_result, adb_store, sim_result, sim_store

    def test_identical_status_steps_and_action_sequence(self):
        adb, _, sim, _ = self._run_both([_tap(), _finish("opened settings")])

        self.assertEqual(adb.status, RunStatus.SUCCEEDED)
        self.assertEqual(sim.status, RunStatus.SUCCEEDED)
        self.assertEqual(adb.steps_count, 2)
        self.assertEqual(sim.steps_count, 2)

        # Action sequence identical regardless of backend.
        adb_types = [s.action.type for s in adb.steps]
        sim_types = [s.action.type for s in sim.steps]
        self.assertEqual(adb_types, [ActionType.TAP, ActionType.FINISH])
        self.assertEqual(adb_types, sim_types)

    def test_only_env_state_differs_between_backends(self):
        """The StepResult schemas must match; the ONLY allowed diff is env_state."""
        adb, adb_store, sim, sim_store = self._run_both([_tap(), _finish()])

        self.assertEqual(len(adb.steps), len(sim.steps))
        for a_step, s_step in zip(adb.steps, sim.steps):
            # Same field set contract.
            self.assertEqual(a_step.status, s_step.status)
            self.assertEqual(a_step.action, s_step.action)
            # env_result is absent on FINISH steps (no execution) but present
            # on action steps; either way it must match across backends.
            if a_step.env_result is None:
                self.assertIsNone(s_step.env_result)
            else:
                self.assertIsNotNone(s_step.env_result)
                self.assertEqual(a_step.env_result.success, s_step.env_result.success)
            # The one genuine backend difference.
            self.assertIsNone(a_step.observation.env_state)
            self.assertIsNotNone(s_step.observation.env_state)

        # Stores agree on the final shape.
        self.assertEqual(adb_store.final_status, sim_store.final_status)
        self.assertEqual(len(adb_store.steps), len(sim_store.steps))


# ---------------------------------------------------------------------------
# Loop semantics
# ---------------------------------------------------------------------------


class RunnerSemanticsTests(unittest.TestCase):
    def test_failed_execution_terminates_run_immediately(self):
        """P1#1: a failed execute() ends the run as FAILED right away, not just
        marking the step. Proved by giving the agent a *second* action that must
        never be reached — if it were, steps_count would be > 1."""
        task = FreeformTask(description="fail", max_steps=5)
        result = Runner(
            ScriptedAgent([_tap(), _tap(), _finish()]),  # only the first runs
            FailingBackend(),
        ).run(task)

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.steps_count, 1)  # stopped at the failed step
        self.assertEqual(result.steps[0].status, "error")
        self.assertIsNotNone(result.steps[0].error)
        self.assertIn("execution failed", result.message)

    def test_request_user_shares_one_resume_point_across_step_and_run(self):
        """P1#2: step and run-level ResumePoint must be the SAME object with ONE
        token — never two tokens minted for one pause."""
        task = FreeformTask(description="ask", max_steps=5)
        result = Runner(
            ScriptedAgent([_request_user("which account?")]), AdbLikeBackend()
        ).run(task)

        self.assertEqual(result.status, RunStatus.WAITING_USER)
        self.assertFalse(result.status.is_terminal)
        self.assertEqual(result.steps_count, 1)
        self.assertEqual(result.steps[0].status, "waiting_user")

        step_rp = result.steps[0].resume_point
        run_rp = result.resume_point
        self.assertIsNotNone(step_rp)
        self.assertIsNotNone(run_rp)
        # Same object identity => same token => resumability is consistent.
        self.assertIs(step_rp, run_rp)
        self.assertEqual(step_rp.resume_token, run_rp.resume_token)
        self.assertEqual(run_rp.paused_at_step, 1)
        self.assertIsNone(run_rp.user_response)
        self.assertEqual(run_rp.pending_action.type, ActionType.REQUEST_USER)

    def test_max_steps_reached_is_failed(self):
        # Agent never finishes; loop exhausts max_steps. Enough actions supplied
        # so the run genuinely hits the limit (not adapter exhaustion).
        agent = ScriptedAgent([_tap(), _tap(), _tap(), _tap()])
        task = FreeformTask(description="loop", max_steps=2)
        result = Runner(agent, AdbLikeBackend()).run(task)

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.message, "Max steps reached")
        self.assertEqual(result.steps_count, 2)

    def test_adapter_exhaustion_is_failed_not_crash(self):
        """P1#3: when the adapter runs out of actions mid-run, the run ends as
        FAILED with a diagnosable message — not a silent green test."""
        task = FreeformTask(description="under-resourced", max_steps=5)
        result = Runner(
            ScriptedAgent([_tap()]),  # max_steps=5 but only 1 action
            AdbLikeBackend(),
        ).run(task)

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.steps_count, 1)
        self.assertIn("error", result.message.lower())

    def test_judge_can_fail_a_finished_task(self):
        """A judge returning passed=False overrides the smoke-success path."""

        class RejectingJudge(Judge):
            def evaluate(self, observation, action, env_result):
                return JudgeResult(
                    passed=False, reason="settings not actually open", judge_type="state"
                )

        task = VerifiableTask(description="打开设置", max_steps=5)
        result = Runner(
            ScriptedAgent([_finish()]),
            AdbLikeBackend(),
            judge=RejectingJudge(),
        ).run(task)

        # Agent finished, but the judge said it failed.
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertIsNotNone(result.final_judge)
        self.assertFalse(result.final_judge.passed)

    def test_none_judge_smoke_success(self):
        """Without an explicit judge, FINISH is a smoke SUCCEEDED (legacy behavior)."""
        task = FreeformTask(description="打开设置", max_steps=5)
        result = Runner(
            ScriptedAgent([_finish("opened settings")]),
            AdbLikeBackend(),
            judge=NoneJudge(),
        ).run(task)

        self.assertEqual(result.status, RunStatus.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
