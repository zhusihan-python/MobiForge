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
