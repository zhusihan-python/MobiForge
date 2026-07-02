# Core Runtime Schemas (Phase 1 Draft)

> Phase 0 deliverable: draft schemas for the six runtime concerns.
> Status: **FROZEN v1** — Gate A (fake-boundary equivalence) and Gate B
> (non-fake `OpenAutoGLMAdapter` + `AdbDeviceEnv` cross the runtime boundary,
> proven context-equivalent to the legacy `PhoneAgent.run()` path via the
> `tests/test_oracle.py` harness) have both passed. All field names and types
> are now locked at v1. Future evolution goes through a `meta.json` schema
> version field.

## Design rules (from ADR-0001)

1. **Optional fields must be genuinely optional.** ADB observations leave
   `env_state` absent; simulation observations populate it. No fake fields.
2. **Coordinates normalize to `[0, 1000]`** — this is **MobiForge's own design
   choice**, not inherited from MobileGym (whose code uses raw pixel ints with a
   screen-size config). Absolute pixels live in the adapter/backend, not in the
   normalized `Action`.
3. **The contract is the MobileGym contract** (action set, task-as-class, judge
   model), adapted to MobiForge boundaries. MobileGym types do not leak into the
   core; a `mobilegym` backend adapter owns translation.
4. **Human debugging first.** Every step must be reconstructable by a person
   reading the trajectory dir, with no log scraping (PRD Story #15, #19).

## Type notation

Schemas are shown as a Python `dataclass`-flavored pseudo-notation for clarity.
The serialized form is JSON. `optional` fields may be `None` or omitted.

---

## 1. Observation

Produced by `EnvBackend.observe()`. Describes what the agent saw before acting.

```python
@dataclass
class Observation:
    id: str                       # unique within a run, e.g. "obs_0003"
    timestamp: str                # ISO-8601 UTC
    screenshot: Optional[ArtifactRef]        # path/ref into run dir
    screenshot_size: Optional[Size]          # {width, height} in px
    accessibility_tree: Optional[dict]       # structured payload, if available
    env_state: Optional[dict]                # structured JSON state (sim only)
    current_app: Optional[str]               # package/bundle id or name
    device: Optional[DeviceMeta]             # device metadata
    is_sensitive: bool                       # screen flagged sensitive
```

**Backend behavior:**

| Field | ADB | Simulation |
|---|---|---|
| `screenshot` | PNG via screencap | rendered frame |
| `screenshot_size` | real pixels | sim pixels |
| `accessibility_tree` | optional (uiautomator dump) | structured UI tree |
| `env_state` | **absent** (or minimal) | full structured JSON |
| `current_app` | `dumpsys` / `adb shell` | sim runtime value |
| `is_sensitive` | heuristic / app allowlist | sim flag |

---

## 2. Action

Produced by `AgentAdapter.act(observation, ...)`. Normalized; backend translates
to its own execution primitive.

```python
@dataclass
class Action:
    type: str                     # one of ACTION_TYPES below
    coordinates: Optional[list[float]]   # normalized [0,1000], e.g. [x,y] or [x1,y1,x2,y2]
    text: Optional[str]           # for type_text / finish / request_user
    app: Optional[str]            # for launch_app (package/bundle id)
    duration_ms: Optional[int]    # for swipe / wait
    raw: Optional[dict]           # original adapter-specific action (provenance)
    safety: Optional[SafetyMeta]  # confirmation metadata for sensitive actions
```

**Action set (12 types — Gate B expansion of the PRD 9-type minimum):**

The PRD minimum is 9 types; the Open-AutoGLM model emits a 14-action output
space, so the set was expanded by 3 (`double_tap`, `long_press`, `noop`) during
Gate B so no model capability is lost. The set is still a subset of MobileGym's
18 `ActionType`s, so ADR-0001's positioning holds.

```text
# Base 9 (PRD minimum)
tap / double_tap / long_press   # coordinates: [x, y]
swipe              # coordinates: [x1, y1, x2, y2]
type_text          # text
home / back        # (no params)
launch_app         # app
wait               # duration_ms
finish             # text: reason/message
request_user       # text: question  -> runner enters WAITING_USER
# Additions (Gate B)
noop               # no env change (Note/Call_API); raw carries the original dict
```

**Open-AutoGLM legacy-dict → normalized-Action mapping** (implemented in
`OpenAutoGLMAdapter`; every Action carries `raw` = the original parsed dict):

| Legacy model output (`do(action=...)` / `finish(...)`) | → ActionType | fields |
|---|---|---|
| `Tap` (element) | `TAP` | coordinates |
| `Double Tap` (element) | `DOUBLE_TAP` | coordinates |
| `Long Press` (element) | `LONG_PRESS` | coordinates |
| `Type` / `Type_Name` (text) | `TYPE_TEXT` | text |
| `Swipe` (start, end) | `SWIPE` | coordinates (4) |
| `Back` | `BACK` | — |
| `Home` | `HOME` | — |
| `Launch` (app) | `LAUNCH_APP` | app |
| `Wait` (duration="x seconds") | `WAIT` | duration_ms (parsed) |
| `Take_over` (message) | `REQUEST_USER` | text |
| `Interact` | `REQUEST_USER` | text (synthesized) |
| `Note` / `Call_API` | `NOOP` | raw=dict |
| `finish` (message) | `FINISH` | text |

Unknown action names raise (the model's action space is known/closed).

**Normalization rules:**

- All coordinates in `coordinates` are normalized to `[0, 1000]` on both axes.
  The backend multiplies by real screen size at execution time.
- `raw` retains the adapter's original parsed action so a trajectory can show
  exactly what the model emitted, even after normalization.
- `safety` is populated when an action matches a sensitive pattern (e.g.
  uninstall, payments, factory reset). The runner consults a confirmation policy
  before executing (PRD Story #16).

### ResumePoint (for `request_user` / `WAITING_USER`)

`request_user` is the only action that does not advance the environment. Per the
PRD, `WAITING_USER` is a **paused, resumable** state, not a terminal one: a CLI
may exit after surfacing it, but API and future platform callers must be able to
resume or cancel. The schema must therefore preserve a resumption point even
though Phase 1 is not required to implement human handoff.

```python
@dataclass
class ResumePoint:
    resume_token: str            # opaque, unique per paused run
    pending_action: Action       # the request_user action awaiting a response
    paused_at_step: int          # step index where the run paused
    user_response: Optional[str] = None   # filled on resume; None while paused
```

- On pause, the runner writes a `ResumePoint` (with `user_response=None`) into
  the trajectory and returns status `waiting_user`.
- On resume, the caller supplies `user_response`; the runner feeds it back into
  the adapter context and continues. On cancel, the run ends as `cancelled`.
- Phase 1 accepts a trivial implementation (resume is a no-op that immediately
  cancels), but the **fields** must exist so later phases do not break the
  persisted trajectory schema.

---

## 3. TaskSpec

Two modes (PRD). Both are inputs to `Runner.run()`. **Both must be JSON-
serializable end to end**, because a trajectory stores its `task` and a future
API/run-index persists and replays task specs. No `Callable` lives in the
serialized form; judges are referenced by name and resolved at run time.

```python
@dataclass
class FreeformTask:
    mode: Literal["freeform"] = "freeform"
    description: str              # natural language, e.g. "打开设置"
    max_steps: Optional[int] = None
    timeout_seconds: Optional[float] = None


@dataclass
class JudgeRef:
    """Serializable reference to a judge, resolved by a JudgeRegistry."""
    judge_type: Literal["none", "rule", "state", "vlm"]
    # Dotted registry key, e.g. "mobiforge.judges.settings_opened".
    # The registry returns a Callable[[Observation, TaskContext], JudgeResult].
    entrypoint: Optional[str] = None
    # Free parameters passed to the resolved callable (e.g. target package,
    # expected state snapshot, VLM model id). Schema is judge-type-specific.
    config: Optional[dict] = None


@dataclass
class VerifiableTask:
    mode: Literal["verifiable"] = "verifiable"
    description: str
    setup: Optional[dict] = None          # state to inject before run (sim)
    setup_actions: Optional[list[Action]] = None  # device actions to prepare (ADB)
    goal: Optional[str] = None            # human-readable success condition
    constraints: list[str] = field(default_factory=list)  # forbidden shortcuts
    judge_ref: Optional[JudgeRef] = None  # serializable; replaces a Callable
    answer_schema: Optional[dict] = None  # AnswerSheet: structured query answer
    max_steps: Optional[int] = None
    timeout_seconds: Optional[float] = None

TaskSpec = Union[FreeformTask, VerifiableTask]
```

**Notes:**

- `FreeformTask` is the CLI smoke path (PRD Story #12). With a `NoneJudge`
  `judge_ref` it behaves like today's plain-string task.
- `VerifiableTask.judge_ref` replaces the earlier `check_goals: Callable`. The
  serialized spec carries only `judge_type + entrypoint + config`; the runtime
  looks up the real function in a `JudgeRegistry` at run time. This mirrors how
  MobileGym addresses tasks by module/class, and it is what makes a spec
  persistable, replayable, and cross-process safe.
- `JudgeRef.config` is the place for a `StateJudge`'s expected state snapshot, a
  `RuleJudge`'s target package, or a `VLMJudge`'s model id. On ADB, where
  structured state is absent, the resolved judge typically falls back to a rule
  on `current_app`/`accessibility_tree` or to an opt-in `VLMJudge`.
- `answer_schema` adopts MobileGym's AnswerSheet: for query tasks the agent
  submits structured answers instead of free text, avoiding string-match failure.

**Registry contract (implemented in `runtime/judge_registry.py`):**

```python
class JudgeRegistry:
    def register(self, entrypoint: str, factory: Callable[[JudgeRef], Judge]) -> None: ...
    def resolve(self, ref: JudgeRef) -> Judge: ...
```

Built-in `none` and `state` judges ship in core. The default `state` resolver
expects `JudgeRef.config["expected_state"]` and returns `StateJudge`. `rule` and
`vlm` judges must provide an explicit `entrypoint` registered by the task suite
or backend adapter.

---

## 4. JudgeResult

Produced by a `Judge`. One per terminal step (or per step if judge runs each step).

```python
@dataclass
class JudgeResult:
    passed: bool
    score: Optional[float]         # 0.0–1.0, optional
    reason: str                    # human-readable
    evidence: Optional[dict]       # structured payload or artifact refs
    judge_type: Literal["none", "rule", "state", "vlm"]
```

**Judge modes (PRD / ADR-0001):**

| Judge | When | Determinism |
|---|---|---|
| `NoneJudge` | manual smoke runs (`FreeformTask` w/o judge) | n/a |
| `RuleJudge` | action/metadata checks (e.g. `current_app` == target) | deterministic |
| `StateJudge` | structured `env_state` (simulation primary) | deterministic, sub-ms |
| `VLMJudge` | real-device visual fallback; **opt-in only** | ~10.2% false-verdict (spike) |

---

## 5. StepResult

One iteration of the runner loop. This is the unit persisted per step in the
trajectory. It is the **explicit schema** that replaces the current
`RunRecorder` reliance on `getattr(StepResult, ...)`.

```python
@dataclass
class StepResult:
    step: int
    observation: Observation
    model_input: Optional[ArtifactRef]     # prompt artifact (if recorded)
    model_output: Optional[str]            # raw model text
    action: Optional[Action]               # normalized action, None if parse failed
    env_result: Optional[EnvResult]        # execution outcome from backend
    judge_result: Optional[JudgeResult]    # if a judge ran this step
    status: Literal["ok", "error", "waiting_user"]
    error: Optional[ErrorInfo]             # structured error if status == error
    resume_point: Optional[ResumePoint]    # present iff status == waiting_user
    duration_ms: int
```

**Why explicit:** the current `RunRecorder.record_step` reads attributes by name
from `PhoneAgent.StepResult`. Once `step()` is split, any adapter/backend pair
must produce identical `StepResult` shape, so the fields become a contract.

---

## 6. Trajectory

The persisted run. Owned by `TrajectoryStore` (the evolved `RunRecorder`).

```python
@dataclass
class Trajectory:
    run_id: str
    task: TaskSpec                          # the spec that was run
    backend: BackendMeta                    # backend name + config snapshot
    agent: AgentMeta                        # adapter name + model config
    status: Literal[
        "succeeded", "failed", "cancelled", "timeout", "waiting_user"
    ]
    steps: list[StepResult]                 # or refs into steps.jsonl
    final_judge_result: Optional[JudgeResult]
    resume_point: Optional[ResumePoint]     # present iff status == waiting_user
    artifacts: ArtifactIndex                # screenshots, logs, model I/O, states
```

**On-disk layout** (unchanged from current spike, now schema-backed):

```text
runs/
└── 2026-06-27_12-00-00_a1b2c3d4_打开设置/
    ├── meta.json          # run_id, task, backend, agent, status, timestamps
    ├── steps.jsonl        # one StepResult per line
    ├── screenshots/
    │   ├── 001.png
    │   └── 002.png
    └── result.json        # final status + final_judge_result + summary
```

---

## Phase 1 validation gates

These schemas are **DRAFT v1** until two distinct gates pass. The tracer bullet
below is the **schema-freeze gate**; it is necessary but not sufficient for PRD
Phase 1, which also requires a non-fake shape to cross the boundary.

**Gate A — schema freeze (tracer bullet):**

> A single fake `AgentAdapter` runs the same `Runner` against two fake
> `EnvBackend` implementations and produces **identical trajectory semantics**
> (same fields populated, same status transitions, same `StepResult` shape).

When Gate A passes, the schemas are **frozen for fake-boundary semantics** —
the field set that the two-fake-backend equivalence relies on is locked, and a
schema version field is added to `meta.json`. Note this is deliberately a
partial freeze: full v1 freeze (all field names and types) requires Gate B,
because only a non-fake shape can validate that the contract fits a real
Open-AutoGLM/ADB adapter and backend, not just doubles of them.

**Gate B — PRD Phase 1 acceptance (non-fake crossing):**

> At least one **non-fake** adapter or backend shape passes through the runtime
> boundary. Concretely: either the transitional `OpenAutoGLMAdapter` (built on
> the extracted `ActionParser`/`ModelPlanner`, per ADR-0001's migration plan) or
> a real/fixture `EnvBackend` (an ADB fixture or a MobileGym-compatible
> observation/action fixture) completes a task through the same `Runner` and
> writes a valid trajectory.

Gate A proves the schema is backend-agnostic against controlled doubles; Gate B
(the PRD's stated Phase 1 criterion) proves the boundary is real, not vacuous,
and is what completes the v1 freeze. Both must pass before Phase 1 is declared
done.

## Migration from current spike

| Current (spike) | Target (this draft) |
|---|---|
| `task: str` | `TaskSpec` (`FreeformTask` / `VerifiableTask`) |
| `PhoneAgent.step()` blob | `AgentAdapter.act()` + `EnvBackend.observe()/execute()` |
| `StepResult` (phone_agent) with `getattr`-bound recorder | explicit `StepResult` schema here |
| success == `step_result.finished` | `JudgeResult.passed` from a `Judge` |
| `RunRecorder` | `TrajectoryStore` (same on-disk layout, schema-backed) |
| global `device_factory` | `EnvBackend` instance passed to runner |
