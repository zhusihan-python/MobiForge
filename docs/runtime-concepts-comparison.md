# Runtime Concepts: Open-AutoGLM vs MobiForge Fork vs MobileGym

> Phase 0 deliverable. Side-by-side comparison of how each system realizes the six
> runtime concerns the PRD identifies. Feeds directly into
> `docs/adr/0001-mobilegym-positioning.md`.

## Reading guide

- **Open-AutoGLM** = the upstream project MobiForge forked from. Provides the
  agent baseline and the real-device control path (`PhoneAgent.step()`).
- **MobiForge fork** = current repository state: Open-AutoGLM + a thin
  `runtime/` spike (`TaskRunner`, `RunRecorder`) that wraps `step()` for trace
  recording, cancellation, timeout, max-steps.
- **MobileGym** = the evaluation platform studied in the Phase 0 spike
  (see `docs/research-spike-mobilegym.md`).

The goal is to see, concern by concern, what each system does well and what
MobiForge should adopt, adapt, or reject.

## Comparison matrix

| Concern | Open-AutoGLM | MobiForge fork | MobileGym |
|---|---|---|---|
| **Agent adapter** | `PhoneAgent` monolith: prompt build + model call + action parse + device exec in one `_execute_step`. | Same monolith, wrapped by `TaskRunner`. No adapter interface. | thin adapters (9 of them), each maps one model's output → the 18-`ActionType` set. |
| **Env backend** | Global `device_factory` → `adb`/`hdc` modules. Observation = screenshot + current_app, captured inside `_execute_step`. | Identical; backend not separable from agent. | Structured-JSON state, three layers (World/Overlay/OS), `setState`/snapshot/fork. Driven by two bridges: Python `bench_env` uses **Playwright**, the frontend harness uses **Puppeteer**. |
| **Task spec** | A plain `str` task description passed to `run(task)`. | Identical (`"打开设置"`). | Python class: `description` + `setup(state)` + `check_goals(state)` + optional `get_answer` (AnswerSheet). |
| **Judge** | Implicit: success == model emits `finish`. No independent verification. | Identical; recorder stores `finished`/`success` flags only. | Deterministic `check_goals()`, sub-ms, no VLM; plus state-diff side-effect check. |
| **Trajectory** | In-memory `_context` list; lost on exit. | `RunRecorder`: `meta.json` + `steps.jsonl` + `screenshots/` + `result.json`. Step record `getattr`-bound to `PhoneAgent.StepResult`. | Full per-step observation/action/reward/state/latency; RL-training-ready. |
| **Reset / reproducibility** | None; device left in whatever state. | None. | JSON state injection; forkable; deterministic resets. |

## What to adopt / adapt / reject

### Adopt (drop-in compatible)

- **MobileGym's action abstraction** — its 18-`ActionType` set is a superset of
  the PRD minimum. Adopt the subset. (Coordinate normalization to `[0,1000]` is
  MobiForge's own addition — MobileGym's code uses raw pixel ints.)
- **MobileGym's task-as-class shape** — `description / setup / check_goals /
  get_answer` maps 1:1 to the PRD `VerifiableTask`. This is the schema to converge
  on.
- **MobileGym's deterministic judge model** — `StateJudge` (structured state) and
  `RuleJudge` (action/metadata) become first-class; VLM judge stays optional
  fallback (10.2% false-verdict rate measured, see spike).
- **MobileGym's adapter pattern** — the ~100-line `autoglm` adapter is the
  template for MobiForge's `OpenAutoGLMAdapter`. It directly answers PRD Open
  Question #3: do not preserve `PhoneAgent.step()`, split it.
- **MobiForge fork's `TaskRunner` control loop** — max-steps, cooperative cancel,
  between-step timeout, terminal states. This is good and stays; it just needs to
  drive an `AgentAdapter` + `EnvBackend` pair instead of `PhoneAgent` directly.

### Adapt (reshape to fit MobiForge boundaries)

- **`RunRecorder` → `TrajectoryStore`.** The current recorder is correct in shape
  (self-contained run dir, JSONL steps, screenshots) but its step fields are
  `getattr`-bound to `PhoneAgent.StepResult` attribute names. Re-bind to an
  explicit `StepResult` schema so any adapter/backend pair produces the same
  trajectory.
- **MobileGym's structured state.** The simulator gives free structured state;
  real ADB does not. The `Observation.env_state` field stays **optional** so ADB
  observations do not have to fake it, and so `StateJudge` degrades to a no-op or
  a VLM fallback on real devices.
- **`PhoneAgent.step()`.** Split along the observe/act seam:
  - observe (screenshot + current_app) → moves **into** `EnvBackend.observe()`
  - model call + parse → moves into `OpenAutoGLMAdapter.act()`
  - execute (tap/swipe/type) → moves into `EnvBackend.execute(action)`
  This split is what makes the same adapter runnable against both ADB and a
  simulation backend.

### Reject (do not carry forward)

- **`PhoneAgent.step()` as the architectural unit.** It conflates perception,
  planning, action parsing, and execution. Keeping it as the boundary would
  make the PRD's two-backend milestone impossible.
- **Implicit model-emits-finish judging.** It is not verification; it cannot
  support benchmarking or safety review (PRD Stories #6, #16, #17). It survives
  only inside `NoneJudge` for manual smoke runs.
- **Global `device_factory` mutable state as the backend.** Swapping backends
  today means mutating a global; this leaks into agent logic (PRD Story #9).
  Replace with an explicit `EnvBackend` instance passed to the runner.
- **Plain-string task as the only task form.** Keep it for `FreeformTask`
  smoke runs (PRD Story #12), but do not let it be the only form.

## Boundary seam (the key diagram)

The comparison makes the seam that PRD Phase 1 must establish concrete. Read it
left to right as a **directed pipeline** for one loop iteration: the Runner
drives EnvBackend first (observe), feeds the Observation to the AgentAdapter
(act), then drives EnvBackend again (execute). Arrows show data flow, not
ownership.

```text
   TaskSpec ──┐
              ▼
         ┌────────┐  1. observe()        ┌──────────────┐
         │ Runner │ ───────────────────▶ │  EnvBackend  │
         │        │ ◀────────────────── │  (ADB / sim) │
         │ loop:  │      Observation     └──────────────┘
         │ observe│                              │
         │ → act  │  2. act(observation)         │
         │ → exec │ ─────┐                       │
         │ → judge│      ▼                       │
         │ → rec  │ ┌──────────────┐             │
         └────────┘ │ AgentAdapter │             │
              │     │ - prompt     │             │
              │     │ - model call │             │
              │     │ - parse→Action              │
              │     └──────────────┘             │
              │            │                     │
              │            ▼ Action              │
              │  3. execute(action)              │
              │ ──────────────────────────────────┘
              │                   EnvResult
              │            ▼
              │     ┌──────────────┐
              └────▶│   Judge      │ (none/rule/state/vlm)
                    └──────────────┘
                            │
                            ▼  StepResult ──▶ TrajectoryStore
```

In words, one iteration is exactly:

```text
Runner → EnvBackend.observe() ──Observation──▶ AgentAdapter.act() ──Action──▶
  EnvBackend.execute() ──EnvResult──▶ Judge ──JudgeResult──▶ TrajectoryStore.record()
```

- **`observe()` lives on `EnvBackend`, never on `AgentAdapter`.** The adapter
  *consumes* an Observation; it does not capture one. (Today's `step()` violates
  this by calling `device_factory.get_screenshot` inside the agent.)
- **`act()` lives on `AgentAdapter`.** It is pure given (observation, context):
  prompt build + model call + parse. No device I/O.
- **`execute()` lives on `EnvBackend`.** It turns a normalized `Action` into the
  backend's primitive (`adb shell input tap` or a Playwright DOM click).
- **Open-AutoGLM / MobiForge fork today:** all the boxes are fused inside
  `PhoneAgent._execute_step()`; `Runner` calls it as one blob. The migration plan
  in ADR-0001 splits this incrementally.
- **MobileGym:** all boxes exist, but `EnvBackend` is a React/Playwright
  simulator and the adapter set is model-specific.
- **MobiForge target:** keep MobiForge's own `Runner` + `TrajectoryStore`, adopt
  MobileGym's adapter pattern and task/judge contracts, and let both ADB and a
  MobileGym-style backend sit behind the same `EnvBackend` interface.

## Why this comparison settles the PRD's core question

The PRD asks whether one agent can run against both a real ADB path and a
simulation path while emitting one trajectory format. The comparison shows:

1. The **runner and trajectory store** already exist in MobiForge and are sound.
2. The **adapter and backend interfaces do not exist** and are the real gap.
3. **MobileGym already solved** the adapter shape, the action set, and the
   task/judge contract — in a form that ports cleanly to Python.
4. Therefore the cheapest correct path is: adopt MobileGym's contract, split
   `PhoneAgent.step()` along the seam, and keep the existing runner.

That conclusion is recorded as the decision in ADR-0001.
