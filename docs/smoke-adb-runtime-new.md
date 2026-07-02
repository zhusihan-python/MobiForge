# Real-ADB Smoke Test: `--runtime new`

> Pre-flight checklist for validating the Phase 2 `--runtime new` path on a real
> Android device. This is the prerequisite for flipping the default (ADR step 4).
> Run it before writing the disk `TrajectoryStore` — execution-layer bugs must be
> isolated from recording-layer ones.

> TODO: Real-device validation is currently blocked because no ADB phone/emulator
> is available in the working environment. Keep this checklist open and run it
> before flipping the runtime default or claiming Phase 2 device coverage.

## Prerequisites

1. **One ADB device connected**, authorized (`adb devices` shows `device`, not
   `unauthorized`). Multi-device: pass `--device-id <serial>`.
2. **Model server reachable** at the configured `--base-url`. AutoGLM-Phone-9B
   via vLLM, or whatever `model_config.model_name` points to. The new path calls
   the same `ModelClient` as legacy, so reuse your working legacy endpoint.
3. **ADB Keyboard installed** on the device (for `type_text`). The legacy path
   needs it; the new path delegates to the same `ActionHandler`, so same need.
4. Run from the repo root with the project venv: `.venv/bin/python main.py ...`

## Minimal smoke command

```bash
.venv/bin/python main.py \
  --runtime new \
  --device-type adb \
  --base-url http://localhost:8000/v1 \
  --model autoglm-phone-9b \
  --task "打开设置" \
  --max-steps 5
```

- Keep `--max-steps 5` low. The goal is one full observe→act→execute cycle, not
  task completion.
- For multi-device: add `--device-id <serial>`.
- If you want to compare against legacy on the same device/task, run the same
  command with `--runtime legacy` (or omit `--runtime`) right after.

## What "passing" looks like

Per step, the new path prints:
```
Step N: Result/Status/Steps/Duration
```
and at the end:
```
Result: <message>
Status: succeeded      # lowercase — the new RunStatus (legacy prints SUCCEEDED)
Steps: <count>
Duration: <ms>
```
- **No `Trace:` line** (new path uses `InMemoryTrajectoryStore`; expected, not a bug).
- A tap visibly happens on screen at the model-chosen location; the screen
  changes; the next observation reflects it.

## Failure points to watch (in likelihood order)

These are the seams where the new path could diverge from legacy on a real
device. Each is annotated with the code path it exercises.

1. **`AdbDeviceEnv.observe()` screenshot capture.** Calls
   `device_factory.get_screenshot()` → `adb shell screencap` + `adb pull` + PIL
   → base64. If this returns the **fallback black 1080×2400 image**
   (`is_sensitive=True`, `"Status: -1"`/`"Failed"` in adb output), the model
   sees a black screen and acts blindly. *Check:* the first step's thinking
   should describe real screen content, not guess.
2. **Coordinate conversion at real screen size.** `AdbDeviceEnv.execute()`
   passes the observed `width/height` to `ActionHandler.execute()`, which does
   `int(coord/1000 * screen_width)`. If `observe()` captured a fallback size
   (1080×2400) but the real device is e.g. 1440×3120, taps land in the wrong
   place. *Check:* a tap on a visible button actually hits it. This couples to
   #1 — a bad screenshot size corrupts every coordinate.
3. **`confirmation_callback` blocking on `input()`.** If the model emits a
   `Tap` with a `message` (sensitive-operation path), the default
   `_default_confirmation` calls `input("Sensitive operation: ...\nConfirm? (Y/N): ")`
   and **blocks the run waiting for console input**. The run appears to hang.
   *Check:* if it hangs mid-step, type `Y`/`N` — if that unblocks it, this is
   the cause. (New path uses the same ActionHandler defaults as legacy, so this
   is parity, not a regression — but it surprises in non-interactive runs.)
4. **`takeover_callback` blocking on `input()`.** If the model emits `Take_over`
   (→ `REQUEST_USER`), `_default_takeover` calls `input("...\nPress Enter...")`
   and blocks. *Check:* same hang signature; Enter unblocks it. Note: in the new
   path this also produces a `WAITING_USER` run status and a `ResumePoint`
   (printed if it occurs), which legacy does not have — that's an observable
   difference, expected.
5. **Model-server connectivity / parse failures.** If `request()` raises, the
   new path's `_run_step` lets it propagate and the Runner records a `FAILED`
   step (legacy wraps it in a failure `StepResult`). *Check:* a connection error
   should end as `Status: failed` with `Task error: ...`, not a Python traceback
   to the console. A raw traceback means the Runner isn't catching it — a bug.

## What to capture if it fails

Paste back, in this order:
1. The **exact command** you ran (including `--device-id` if used).
2. **`adb devices`** output (device authorized?).
3. The **full stdout** from the run, from header to final `Status:` line.
4. If it hung: **where** (which step, last printed line) and **what unblocked**
   it (Y/N, Enter, nothing).
5. **One screenshot** the device was showing when it went wrong (so I can see
   whether the model was acting on real content or a black screen).

## Known-gaps to NOT report as bugs (this batch)

- No `Trace:` line / no run directory written (disk `TrajectoryStore` is the
  next batch).
- `WAITING_USER` / `ResumePoint` printed on takeover — expected new-path
  behavior, legacy doesn't have it.
- iOS/HDC rejected with a notice — by design (`--runtime new` is ADB-only).

## After a green smoke

The default flip (ADR step 4) becomes safe to plan: change `--runtime` default
to `new`, keep `--runtime legacy` behind the flag for one release. Then disk
`TrajectoryStore` to restore the `Trace:` line.
