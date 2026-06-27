# Emulator Runner

The runtime layer wraps the upstream `PhoneAgent.step()` API without moving
device actions out of `phone_agent/`.

## Run a recorded task

```bash
python main.py \
  --device-type adb \
  --device-id emulator-5554 \
  --base-url http://localhost:8000/v1 \
  --model autoglm-phone-9b \
  --max-steps 30 \
  --timeout 300 \
  "打开设置"
```

Task recording is enabled by default. Use `--runs-dir PATH` to choose another
location or `--no-record` to disable it.

Each task creates:

```text
runs/
└── 2026-06-27_12-00-00_a1b2c3d4_打开设置/
    ├── meta.json
    ├── steps.jsonl
    ├── screenshots/
    │   ├── 001.png
    │   └── 002.png
    └── result.json
```

`runtime.task_runner.TaskRunner` is the shared execution boundary intended for
the CLI and the future API server. It currently provides maximum-step handling,
cooperative cancellation, between-step timeout checks, terminal task statuses,
and trace recording.
