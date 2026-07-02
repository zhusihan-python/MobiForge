"""Simulator task fixtures for dogfood and early Phase 3 validation."""

from __future__ import annotations

from typing import Iterable

from runtime.boundary import AgentAdapter
from runtime.judges import StateJudge
from runtime.schemas import Action, ActionType, Observation, VerifiableTask
from runtime.sim_backends import SimulatedDeviceEnv
from runtime.task_suite import TaskCase, TaskSuiteResult, TaskSuiteRunner


class ScriptedAgentAdapter(AgentAdapter):
    """Deterministic adapter for simulator fixtures.

    This is not a model replacement. It is a small fixture adapter that lets the
    runtime dogfood task specs, backends, judges, and summaries without a model
    server.
    """

    def __init__(self, actions: Iterable[Action]) -> None:
        self._actions = list(actions)
        self._index = 0
        self.observations: list[Observation] = []

    def reset(self, task: str) -> None:
        self._index = 0
        self.observations = []

    def act(self, observation: Observation) -> Action:
        self.observations.append(observation)
        if self._index >= len(self._actions):
            raise RuntimeError("script exhausted")
        action = self._actions[self._index]
        self._index += 1
        return action


def make_sim_smoke_cases() -> list[TaskCase]:
    """Return the built-in simulator smoke suite.

    These are intentionally tiny but representative: app launch, text entry, and
    navigation all mutate structured state that a deterministic judge can verify.
    """
    return [
        TaskCase(
            id="sim.open_settings",
            task=VerifiableTask(
                description="open settings in simulator",
                setup={"current_app": "home"},
                goal="current_app is Settings",
                max_steps=5,
            ),
            judge=StateJudge({"current_app": "Settings"}),
            tags=("sim", "launch"),
            metadata={
                "script": [
                    _launch("Settings"),
                    _finish("opened settings"),
                ]
            },
        ),
        TaskCase(
            id="sim.type_note",
            task=VerifiableTask(
                description="type text into notes in simulator",
                setup={"current_app": "home"},
                goal="Notes is open and text_input is hello",
                max_steps=5,
            ),
            judge=StateJudge({"current_app": "Notes", "text_input": "hello"}),
            tags=("sim", "input"),
            metadata={
                "script": [
                    _launch("Notes"),
                    Action(type=ActionType.TYPE_TEXT, text="hello"),
                    _finish("typed hello"),
                ]
            },
        ),
        TaskCase(
            id="sim.back_navigation",
            task=VerifiableTask(
                description="navigate back from browser to settings in simulator",
                setup={"current_app": "home"},
                goal="back returns from Browser to Settings",
                max_steps=6,
            ),
            judge=StateJudge({"current_app": "Settings"}),
            tags=("sim", "navigation"),
            metadata={
                "script": [
                    _launch("Settings"),
                    _launch("Browser"),
                    Action(type=ActionType.BACK),
                    _finish("back to settings"),
                ]
            },
        ),
    ]


def run_sim_smoke_suite(cases: Iterable[TaskCase] | None = None) -> TaskSuiteResult:
    """Run the built-in simulator smoke suite."""
    suite = TaskSuiteRunner(
        adapter_factory=_scripted_adapter_factory,
        backend_factory=lambda case: SimulatedDeviceEnv(),
    )
    return suite.run(cases if cases is not None else make_sim_smoke_cases())


def _scripted_adapter_factory(case: TaskCase) -> ScriptedAgentAdapter:
    script = case.metadata.get("script")
    if not isinstance(script, list):
        raise ValueError(f"Task case {case.id!r} is missing a script list")
    return ScriptedAgentAdapter(script)


def _launch(app: str) -> Action:
    return Action(type=ActionType.LAUNCH_APP, app=app)


def _finish(message: str) -> Action:
    return Action(type=ActionType.FINISH, text=message)
