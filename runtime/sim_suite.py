"""Simulator task fixtures for dogfood and early Phase 3 validation."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from runtime.boundary import AgentAdapter
from runtime.schemas import Action, Observation
from runtime.sim_backends import SimulatedDeviceEnv
from runtime.task_fixtures import load_task_case, load_task_cases_from_dir
from runtime.task_suite import TaskCase, TaskSuiteResult, TaskSuiteRunner


SIM_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tasks" / "sim"
_DEFAULT_SIM_FIXTURES = (
    "open_settings.json",
    "type_note.json",
    "back_navigation.json",
    "browser_search.json",
    "form_fill.json",
    "multi_step_navigation.json",
)


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
    """Return the built-in simulator smoke suite from JSON fixtures.

    These are intentionally tiny but representative: app launch, text entry,
    browser search, form submission, and navigation all mutate structured state
    that a deterministic judge can verify.
    """
    return load_sim_smoke_cases()


def load_sim_smoke_cases(directory: str | Path | None = None) -> list[TaskCase]:
    """Load simulator smoke cases from a fixture directory."""
    fixture_dir = Path(directory) if directory is not None else SIM_FIXTURES_DIR
    if directory is None:
        return [load_task_case(fixture_dir / name) for name in _DEFAULT_SIM_FIXTURES]
    return load_task_cases_from_dir(fixture_dir)


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
