"""JudgeRef registry resolution tests."""

import unittest

from runtime import (
    JudgeRef,
    JudgeRegistry,
    NoneJudge,
    StateJudge,
    VerifiableTask,
    build_default_judge_registry,
    resolve_task_judge,
)
from runtime.boundary import Judge
from runtime.schemas import JudgeResult


class AlwaysPassJudge(Judge):
    def evaluate(self, observation, action, env_result):
        return JudgeResult(passed=True, reason="custom", judge_type="rule")


class JudgeRegistryTests(unittest.TestCase):
    def test_default_registry_resolves_none_judge(self):
        registry = build_default_judge_registry()

        judge = registry.resolve(JudgeRef(judge_type="none"))

        self.assertIsInstance(judge, NoneJudge)

    def test_default_registry_resolves_state_judge(self):
        registry = build_default_judge_registry()

        judge = registry.resolve(
            JudgeRef(
                judge_type="state",
                config={"expected_state": {"current_app": "Settings"}},
            )
        )

        self.assertIsInstance(judge, StateJudge)
        self.assertEqual(judge.expected_state, {"current_app": "Settings"})

    def test_state_judge_requires_expected_state_config(self):
        registry = build_default_judge_registry()

        with self.assertRaises(ValueError):
            registry.resolve(JudgeRef(judge_type="state", config={}))

    def test_custom_entrypoint_can_resolve_rule_judge(self):
        registry = build_default_judge_registry()
        registry.register("tests.always_pass", lambda ref: AlwaysPassJudge())

        judge = registry.resolve(
            JudgeRef(judge_type="rule", entrypoint="tests.always_pass")
        )

        self.assertIsInstance(judge, AlwaysPassJudge)

    def test_rule_without_entrypoint_has_clear_error(self):
        registry = build_default_judge_registry()

        with self.assertRaises(KeyError):
            registry.resolve(JudgeRef(judge_type="rule"))

    def test_resolve_task_judge_uses_task_judge_ref(self):
        task = VerifiableTask(
            description="open settings",
            judge_ref=JudgeRef(
                judge_type="state",
                config={"expected_state": {"current_app": "Settings"}},
            ),
        )

        judge = resolve_task_judge(task, build_default_judge_registry())

        self.assertIsInstance(judge, StateJudge)

    def test_resolve_task_judge_returns_none_without_ref(self):
        task = VerifiableTask(description="manual check")

        self.assertIsNone(resolve_task_judge(task, build_default_judge_registry()))


if __name__ == "__main__":
    unittest.main()
