# Simulator Task Fixtures

This directory contains JSON task-case fixtures for the simulator smoke suite.
Each file is a `TaskCase` envelope:

```json
{
  "id": "sim.open_settings",
  "tags": ["sim", "launch"],
  "task": {
    "mode": "verifiable",
    "description": "open settings in simulator",
    "judge_ref": {
      "judge_type": "state",
      "config": {
        "expected_state": {"current_app": "Settings"}
      }
    },
    "setup": {"current_app": "home"},
    "goal": "current_app is Settings",
    "max_steps": 5
  },
  "metadata": {
    "script": [
      {"type": "launch_app", "app": "Settings"},
      {"type": "finish", "text": "opened settings"}
    ]
  }
}
```

`task` is the serializable runtime task spec. `metadata.script` is only for the
deterministic simulator dogfood adapter; it is not part of the persisted
`TaskSpec`.

## Built-in Passing Fixtures

- `open_settings.json`: launch-app smoke.
- `type_note.json`: text entry smoke.
- `back_navigation.json`: app-level back navigation.
- `browser_search.json`: focused text entry plus browser submit state.
- `form_fill.json`: multi-field form entry plus submission state.
- `multi_step_navigation.json`: in-app navigation stack plus back behavior.

`diagnostics/` contains intentionally failing fixtures for report triage checks.
Run them explicitly with `examples/sim_smoke_suite.py --tasks-dir
tasks/sim/diagnostics`.

## Script Hints

The simulator recognizes a few `metadata.script[].raw.target` hints for
deterministic task fixtures:

- `search_box`: focus `browser.query`.
- `search_submit`: mark browser results as visible.
- `field:<name>`: focus `form.fields.<name>`.
- `submit_form`: mark the form submitted and capture `last_submission`.
- `nav:<page>`: navigate to an in-app page.

These hints are simulator dogfood metadata only. Production task specs should
keep success criteria in `task.judge_ref.config.expected_state`.
