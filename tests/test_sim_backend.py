"""Phase 3 tracer bullet: simulation backend + deterministic state judge.

No real device, model server, or MobileGym dependency is required. These tests
prove the runtime can already execute a verifiable task against a structured
state backend, leaving the heavier MobileGym adapter as a later optional layer.
"""

import unittest

from runtime import (
    Action,
    ActionType,
    AgentAdapter,
    FreeformTask,
    Runner,
    RunStatus,
    SimulatedDeviceEnv,
    StateJudge,
    VerifiableTask,
)


class ScriptedAgent(AgentAdapter):
    def __init__(self, actions):
        self._actions = list(actions)
        self._iter = None

    def reset(self, task):
        self._iter = iter(self._actions)

    def act(self, observation):
        return next(self._iter)


def _launch(app):
    return Action(type=ActionType.LAUNCH_APP, app=app)


def _finish(message="done"):
    return Action(type=ActionType.FINISH, text=message)


def _tap_target(target):
    return Action(type=ActionType.TAP, raw={"target": target})


class SimulatedDeviceEnvTests(unittest.TestCase):
    def test_observe_exposes_structured_state_and_sim_metadata(self):
        env = SimulatedDeviceEnv(initial_state={"current_app": "Settings"})

        obs = env.observe()

        self.assertEqual(obs.current_app, "Settings")
        self.assertEqual(obs.env_state["current_app"], "Settings")
        self.assertEqual(obs.device.backend, "sim")
        self.assertEqual(obs.screenshot_size.width, 1000)

    def test_verifiable_task_setup_overlays_initial_state(self):
        env = SimulatedDeviceEnv(initial_state={"current_app": "home"})
        task = VerifiableTask(
            description="preload app",
            setup={"current_app": "Browser", "task_data": {"query": "cats"}},
        )

        env.reset()
        env.apply_task_setup(task)
        obs = env.observe()

        self.assertEqual(obs.env_state["current_app"], "Browser")
        self.assertEqual(obs.env_state["task_data"]["query"], "cats")

    def test_launch_home_back_and_type_mutate_state(self):
        env = SimulatedDeviceEnv()

        env.execute(_launch("Settings"))
        self.assertEqual(env.observe().env_state["current_app"], "Settings")

        env.execute(Action(type=ActionType.TYPE_TEXT, text="hello"))
        self.assertEqual(env.observe().env_state["text_input"], "hello")

        env.execute(Action(type=ActionType.HOME))
        self.assertEqual(env.observe().env_state["current_app"], "home")

        env.execute(Action(type=ActionType.BACK))
        self.assertEqual(env.observe().env_state["current_app"], "Settings")

    def test_browser_search_updates_structured_state(self):
        env = SimulatedDeviceEnv()

        env.execute(_launch("Browser"))
        env.execute(_tap_target("search_box"))
        env.execute(Action(type=ActionType.TYPE_TEXT, text="mobiforge"))
        env.execute(_tap_target("search_submit"))
        state = env.observe().env_state

        self.assertEqual(state["current_app"], "Browser")
        self.assertEqual(state["current_page"], "results")
        self.assertEqual(state["browser"]["query"], "mobiforge")
        self.assertTrue(state["browser"]["results_visible"])
        self.assertTrue(state["browser"]["submitted"])

    def test_form_targets_focus_fields_and_submit(self):
        env = SimulatedDeviceEnv()

        env.execute(_launch("Forms"))
        env.execute(_tap_target("field:name"))
        env.execute(Action(type=ActionType.TYPE_TEXT, text="Ada Lovelace"))
        env.execute(_tap_target("field:email"))
        env.execute(Action(type=ActionType.TYPE_TEXT, text="ada@example.com"))
        env.execute(_tap_target("submit_form"))
        state = env.observe().env_state

        self.assertEqual(state["form"]["fields"]["name"], "Ada Lovelace")
        self.assertEqual(state["form"]["fields"]["email"], "ada@example.com")
        self.assertTrue(state["form"]["submitted"])
        self.assertEqual(state["form"]["last_submission"], state["form"]["fields"])
        self.assertEqual(state["current_page"], "submitted")

    def test_in_app_navigation_back_precedes_app_history_back(self):
        env = SimulatedDeviceEnv(
            initial_state={"app_pages": {"Workspace": "dashboard"}}
        )

        env.execute(_launch("Workspace"))
        env.execute(_tap_target("nav:projects"))
        env.execute(_tap_target("nav:project_alpha"))
        env.execute(Action(type=ActionType.BACK))
        state = env.observe().env_state

        self.assertEqual(state["current_app"], "Workspace")
        self.assertEqual(state["current_page"], "projects")
        self.assertEqual(state["navigation"]["last_page"], "projects")


class StateJudgeTests(unittest.TestCase):
    def test_state_judge_matches_recursive_subset(self):
        env = SimulatedDeviceEnv(
            initial_state={"current_app": "Browser", "task_data": {"query": "cats", "page": 1}}
        )
        judge = StateJudge({"task_data": {"query": "cats"}})

        result = judge.evaluate(env.observe(), None, None)

        self.assertTrue(result.passed)
        self.assertEqual(result.judge_type, "state")

    def test_state_judge_reports_missing_state(self):
        judge = StateJudge({"current_app": "Settings"})

        result = judge.evaluate(
            observation=type("Obs", (), {"env_state": None})(),
            action=None,
            env_result=None,
        )

        self.assertFalse(result.passed)
        self.assertIn("no env_state", result.reason)


class SimulatedRunnerTests(unittest.TestCase):
    def test_verifiable_task_succeeds_when_agent_finishes_on_expected_state(self):
        agent = ScriptedAgent([_launch("Settings"), _finish("opened settings")])
        env = SimulatedDeviceEnv()
        task = VerifiableTask(
            description="open settings",
            setup={"current_app": "home"},
            goal="current_app is Settings",
            max_steps=5,
        )

        result = Runner(
            agent,
            env,
            judge=StateJudge({"current_app": "Settings"}),
        ).run(task)

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.steps_count, 2)
        self.assertEqual(result.steps[0].observation.env_state["current_app"], "home")
        self.assertEqual(result.steps[1].observation.env_state["current_app"], "Settings")
        self.assertTrue(result.final_judge.passed)

    def test_verifiable_task_fails_when_finish_state_does_not_match(self):
        agent = ScriptedAgent([_launch("Browser"), _finish("done")])
        result = Runner(
            agent,
            SimulatedDeviceEnv(),
            judge=StateJudge({"current_app": "Settings"}),
        ).run(FreeformTask("open settings", max_steps=5))

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertFalse(result.final_judge.passed)


if __name__ == "__main__":
    unittest.main()
