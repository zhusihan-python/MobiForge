# PRD: MobiForge Agent Runtime

## Problem Statement

MobiForge is currently a fork of Open-AutoGLM with working phone-agent primitives for ADB, HDC, and iOS. The near-term temptation is to add a task runner, device pool, API server, and web console directly on top of the fork. That path can prove a demo quickly, but it risks hard-coding the project around one execution path before the core product boundary is understood.

The larger problem is that mobile agents need two different kinds of infrastructure:

- A real-device execution path that can operate Android emulators, Android phones, and later other device types.
- A verifiable evaluation path that can run many tasks reproducibly, record trajectories, and judge success without relying only on visual inspection.

Open-AutoGLM gives MobiForge a useful agent and device-control baseline. MobileGym-like systems show a more advanced direction for environment abstraction, structured state, task definitions, deterministic judging, and high-throughput rollout. MobiForge needs a product and architecture that can use both ideas without becoming a tangled fork or a benchmark-only research harness.

## Primary User And First Release Goal

The first release is for agent/runtime developers who want to evaluate Open-AutoGLM-compatible mobile agents across a real Android execution path and a simulation-style execution path.

The first release is successful when one runtime loop can run:

- one Open-AutoGLM-derived agent adapter,
- one ADB-backed Android environment,
- one MobileGym-compatible or MobileGym-inspired simulation backend,
- one shared trajectory format,
- and at least one verifiable task judged without manual screenshot inspection.

Future platform users, web-console users, schedulers, and multi-device operators matter, but they are not the primary audience for this PRD.

## Solution

Build MobiForge as an Agent Runtime that separates five concerns:

- Agent Adapter: converts a model or upstream agent into a stable `observe -> act` interface.
- Env Backend: executes actions in a real or simulated mobile environment.
- Task Spec: describes freeform and verifiable mobile tasks.
- Judge: evaluates whether a task succeeded, using rule, state, VLM, or no judging.
- Trajectory Store: records observations, actions, model I/O, environment state, screenshots, timing, and judge results.

The first product milestone should not be a web platform. It should be a shared runtime loop where the same agent adapter can run against both a real ADB environment and a simulation-style environment, while emitting one trajectory format.

This reframes MobiForge from:

```text
Open-AutoGLM fork + device runner + platform features
```

to:

```text
Mobile agent runtime with Open-AutoGLM as the first agent adapter,
ADB as the first real-device backend,
and a MobileGym-compatible simulation path as the first verifiable backend.
```

## User Stories

1. As an agent developer, I want to run an Open-AutoGLM-compatible agent through a stable adapter, so that future runtime changes do not require rewriting the upstream agent.

2. As an agent developer, I want to swap the environment backend between ADB and MobileGym-style simulation, so that I can compare real-device behavior with reproducible simulated behavior.

3. As an evaluation engineer, I want every task run to produce a trajectory, so that failures can be diagnosed after the run.

4. As an evaluation engineer, I want each trajectory step to include screenshots, actions, model output, timing, and status, so that I can identify whether a failure came from perception, planning, action conversion, or environment state.

5. As a benchmark author, I want task specs to include setup and success conditions, so that tasks can be verified rather than manually inspected.

6. As a researcher, I want a deterministic judge when structured state is available, so that evaluation does not depend on a VLM judge for every task.

7. As a researcher, I want VLM judging as a fallback for real-device tasks, so that tasks can still be evaluated when structured app state is unavailable.

8. As a model evaluator, I want to batch-run a small smoke suite, so that I can track success rate, average steps, average duration, and failure reasons across model versions.

9. As a platform engineer, I want real-device execution to remain isolated behind an environment interface, so that later device pool and scheduling work does not leak into agent logic.

10. As a platform engineer, I want MobileGym integration to be optional, so that the core runtime can still operate in deployments that only need real devices.

11. As a maintainer, I want the Open-AutoGLM fork boundary to stay small, so that upstream updates remain feasible.

12. As a CLI user, I want to run a freeform task like "打开设置", so that the simplest path remains useful for manual smoke testing.

13. As a CLI user, I want to run a verifiable task spec file, so that success can be judged and recorded automatically.

14. As a future API user, I want the CLI and API server to call the same runner, so that platform features do not duplicate execution logic.

15. As a debugging user, I want every failure to retain the last observation and action, so that no run fails as an opaque console log.

16. As a safety reviewer, I want sensitive or high-risk actions to pass through a confirmation policy, so that automation can be stopped before irreversible operations.

17. As a task author, I want task definitions to describe constraints and side effects, so that the runtime can distinguish successful completion from unsafe shortcuts.

18. As a future scheduler author, I want task runs to have explicit terminal states, so that queues and device locks can be built on top later.

19. As a future web-console user, I want trajectory data to be structured and stable, so that UI pages can show run detail without scraping logs.

20. As a project owner, I want early milestones to measure runtime quality rather than UI completeness, so that the platform grows from a reliable execution and evaluation core.

## Implementation Decisions

- MobiForge will treat Open-AutoGLM as the first supported agent, not as the whole product architecture.

- The initial deep modules are:
  - `AgentAdapter`
  - `EnvBackend`
  - `TaskSpec`
  - `Judge`
  - `TrajectoryStore`
  - `Runner`

- `AgentAdapter` is responsible for model calls, prompt formatting, context handling, and converting model output into a runtime action.

- `EnvBackend` is responsible for reset, observation capture, action execution, optional structured state retrieval, and environment health.

- `TaskSpec` supports two modes:
  - `FreeformTask`, for natural-language tasks that may not have an automatic judge.
  - `VerifiableTask`, for tasks with setup, goal, constraints, max steps, and judge configuration.

- `Judge` is pluggable. The first judge modes are:
  - `NoneJudge`, for manual smoke runs.
  - `RuleJudge`, for simple action or metadata checks.
  - `StateJudge`, for MobileGym-style structured state.
  - `VLMJudge`, for a later real-device visual verification spike when structured state is unavailable.

- `TrajectoryStore` owns the persistent run format. It records step-level screenshots, observations, normalized actions, raw model output, parsed model output, timing, environment state, errors, and judge results.

- `Runner` owns the task state machine. Its first terminal states are:
  - `SUCCEEDED`
  - `FAILED`
  - `CANCELLED`
  - `TIMEOUT`

- `WAITING_USER` is a paused, resumable state rather than a terminal state. A CLI can exit after surfacing it, but API and future platform callers must be able to resume or cancel the run.

- The existing runtime spike can be reused as a trajectory and runner prototype, but it should not become the final architecture until the adapter/backend boundary is reviewed.

- ADB support should move toward an `AdbDeviceEnv` boundary rather than growing inside `main.py`.

- MobileGym concepts should be evaluated as either:
  - a directly integrated backend, or
  - a reference implementation whose task and trajectory concepts inform MobiForge's own backend interface.

- The PRD does not assume direct MobileGym integration until the Phase 0 ADR is complete. Until then, "MobileGym-compatible" means compatible with the useful concepts: simulation backend, structured state, task setup, deterministic judge, and high-throughput rollout.

- The first milestone should prove one shared runtime loop over two backend shapes: ADB and simulation-style stateful environments.

- The CLI remains the first user interface. API server and web console are intentionally delayed until runtime and trajectory schemas stabilize.

- The project should prefer schema compatibility with MobileGym concepts where useful, but avoid copying any interface before running a local spike and reviewing the mismatch with real-device ADB execution.

- `PhoneAgent.step()` should be treated as a transitional legacy adapter. The target `OpenAutoGLMAdapter` should eventually separate model/prompt/action parsing from observation capture and action execution.

## Core Runtime Schemas

The exact serialized format may evolve, but Phase 1 must preserve these conceptual fields.

### Observation

```text
Observation
  id: string
  timestamp: string
  screenshot: optional artifact reference
  screenshot_size: optional { width: int, height: int }
  accessibility_tree: optional structured payload
  env_state: optional structured payload
  current_app: optional string
  device: optional device metadata
  is_sensitive: bool
```

ADB observations will initially rely on screenshots and coarse current-app metadata. Simulation observations may include structured state. The runtime must allow both without forcing fake fields.

### Action

```text
Action
  type: string
  coordinates: optional normalized or absolute coordinates
  text: optional string
  app: optional string
  duration_ms: optional int
  raw: original adapter-specific action
  safety: optional confirmation metadata
```

The minimum action set is `tap`, `swipe`, `type_text`, `home`, `back`, `launch_app`, `wait`, `finish`, and `request_user`.

### Step Result

```text
StepResult
  step: int
  observation: Observation
  model_input: optional artifact reference
  model_output: optional raw text
  action: optional Action
  env_result: optional execution result
  judge_result: optional JudgeResult
  status: ok | error | waiting_user
  error: optional structured error
  duration_ms: int
```

### Judge Result

```text
JudgeResult
  passed: bool
  score: optional float
  reason: string
  evidence: optional structured payload or artifact references
  judge_type: none | rule | state | vlm
```

### Trajectory

```text
Trajectory
  run_id: string
  task: TaskSpec
  backend: backend metadata
  agent: agent metadata
  status: succeeded | failed | cancelled | timeout | waiting_user
  steps: list of StepResult references
  final_judge_result: optional JudgeResult
  artifacts: screenshots, logs, model I/O, states
```

The trajectory must be useful for human debugging first, while retaining enough structure for benchmark analytics later.

## Testing Decisions

- Tests should focus on external behavior of runtime boundaries, not internal implementation details.

- `Runner` tests should verify state transitions, max-step handling, cancellation, timeout, failure propagation, and result persistence.

- `TrajectoryStore` tests should verify the persisted schema and that a failed run retains enough data to diagnose the previous step.

- `AgentAdapter` tests should use fake model responses to verify parsing and action normalization.

- `EnvBackend` tests should use fake environments first, then add opt-in integration tests for ADB and MobileGym.

- `Judge` tests should verify deterministic success and failure behavior using fixture observations and states.

- The first smoke suite should include:
  - open settings
  - browser search
  - install and open a test APK
  - Chinese text input
  - a 3-step in-app task

- Integration tests requiring devices, emulators, model servers, or MobileGym services should be marked opt-in and skipped by default.

- A good acceptance test for Phase 1 is: the same fake agent can run against two fake backends and produce identical trajectory semantics.

## Out of Scope

- Web console.

- Multi-device scheduler.

- Cloud phone orchestration.

- User accounts, permissions, audit logs, and billing.

- Full iOS support beyond preserving the current upstream path.

- HarmonyOS expansion beyond preserving the current HDC path.

- Local deployment or fine-tuning of AutoGLM-Phone-9B.

- Large-scale reinforcement learning infrastructure.

- APK marketplace, script marketplace, report dashboards, and project management features.

## Milestones

### Phase 0: Research and Architecture Decision

Deliverables:

- MobileGym local spike notes.
- Comparison of Open-AutoGLM, current MobiForge fork, and MobileGym runtime concepts.
- ADR deciding whether MobileGym is a backend dependency, a reference architecture, or both.
- Draft schemas for observation, action, task spec, judge result, and trajectory step.

Acceptance criteria:

- The team can explain how one agent runs against ADB and MobileGym-style environments.
- The team can identify which MobileGym concepts are adopted, adapted, or rejected.
- No further platform features are implemented before this decision is made.

### Phase 1: Runtime Boundary

Deliverables:

- `AgentAdapter` interface.
- `EnvBackend` interface.
- `Runner` using adapter and backend interfaces.
- Fake backend and fake agent tests.
- Transitional Open-AutoGLM adapter spike that proves the current upstream action format can be normalized.
- One concrete backend-shape spike using either the current ADB screenshot/action path or a MobileGym-compatible observation/action fixture.

Acceptance criteria:

- A fake agent can complete a fake task through the shared runner.
- A fake failure produces a diagnosable trajectory.
- CLI behavior can be implemented as a thin wrapper over the same runner.
- At least one non-fake adapter or backend shape passes through the runtime boundary.
- The Phase 1 implementation demonstrates where current `PhoneAgent.step()` behavior must be split before full backend portability.

### Phase 2: ADB Device Backend

Deliverables:

- `AdbDeviceEnv` for Android emulator and real-device execution.
- Screenshot, tap, swipe, text input, home/back, app launch, and health checks behind the backend boundary.
- Basic reset policy.

Acceptance criteria:

- `emulator-5554` can run "打开设置".
- Each step records pre-action screenshot, model output, normalized action, execution result, and timing.
- Failed runs retain the last useful screenshot and action.

### Phase 3: Simulation Backend Spike

Deliverables:

- Minimal MobileGym-compatible backend or adapter, if the Phase 0 ADR chooses direct integration.
- Otherwise, a MobileGym-inspired simulation backend fixture with structured state and deterministic judging.
- Mapping between MobileGym observations/actions and MobiForge runtime schemas.
- At least one verifiable task using structured state judge.

Acceptance criteria:

- The same runner can execute a MobileGym-style task.
- The judge can mark success/failure without manual inspection.
- Trajectory semantics match the ADB backend closely enough for shared analysis.

### Phase 4: Verifiable Task Suite

Deliverables:

- Small task-spec format.
- Five smoke tasks.
- Batch runner summary with success rate, average steps, average duration, and failure reason.

Acceptance criteria:

- A model or fake agent can be evaluated over the smoke suite.
- Results are comparable across runs.
- Each failed task has enough trajectory data for review.

### Future Platform PRD: API And Run Index

Deliverables:

- Lightweight API server.
- SQLite or equivalent local run index.
- Device/task status endpoints.

Acceptance criteria:

- API creates tasks through the same runtime.
- API can query task status and retrieve trajectory artifacts.
- Single-device concurrency conflicts are prevented.

This section is not in scope for the first runtime PRD. It is retained to show the intended platform direction once runtime schemas stabilize.

## Risks

- The project may over-abstract before enough real-device and MobileGym behavior is understood.

- MobileGym schemas may not map cleanly onto real Android ADB execution.

- VLM judging may be too unreliable for real-device validation unless constrained carefully.

- Keeping compatibility with upstream Open-AutoGLM may conflict with runtime boundary changes.

- Device input, especially Chinese text input, may dominate practical reliability more than higher-level architecture.

- A task schema that is too rich too early may slow down simple smoke testing.

- A trajectory schema that is too narrow will make future benchmark and web-console work painful.

## Open Questions

- Should MobileGym be a required development dependency, an optional backend, or only a benchmark integration?

- Should MobiForge adopt MobileGym's task schema directly or define a smaller compatibility layer?

- Should `PhoneAgent.step()` be wrapped as-is, or should prompt/model/action parsing be moved behind a cleaner `OpenAutoGLMAdapter`?

- What is the minimum normalized action schema that works for both ADB and MobileGym?

- What structured state, if any, can be extracted from real Android apps to reduce reliance on VLM judging?

- Should the first trajectory schema optimize for human debugging, benchmark analytics, or future training data?

- How strict should sensitive-action confirmation be in the first runtime?

- Should the current runtime spike remain in the repository while the PRD is reviewed, or be reverted before the architecture branch begins?

## Further Notes

MobiForge should keep the original principle of "先稳执行内核，再长出平台能力", but redefine "执行内核" to include evaluation boundaries, not only ADB control. The more precise principle is:

```text
先稳 Agent/Env/Task/Judge/Trajectory 边界，
再接真实设备和仿真环境，
最后长出 API、Web、队列和设备池。
```

The runtime should make simple tasks easy and verifiable tasks possible. If those two paths remain aligned, the project can support demos, debugging, benchmarking, and future platform work without choosing one too early.
