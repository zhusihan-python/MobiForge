# ADR-0001: MobileGym Positioning

- **Status:** Accepted
- **Date:** 2026-06-27
- **Phase:** Phase 0 — Research and Architecture Decision
- **Supersedes:** none
- **Supersedes question:** PRD Open Questions #1, #2, #3
- **Depends on:** `docs/research-spike-mobilegym.md`, `docs/runtime-concepts-comparison.md`

## Context

The PRD blocks all platform-feature work until a Phase 0 decision is made on how
MobileGym relates to MobiForge. Three positions were on the table:

1. MobileGym as a **required** development dependency (first-class backend).
2. MobileGym as an **optional** backend.
3. MobileGym as **reference only** — copy concepts, depend on nothing.

The PRD's first-release success criterion is that one runtime loop runs one agent
adapter against **both** an ADB backend and a MobileGym-compatible simulation
backend, emitting one shared trajectory format. The decision had to be informed
by a real spike, not by assumptions.

> **Scope note on evidence:** the spike is a *research* spike — source code of a
> shallow clone was read directly (`[SRC]` findings in
> `docs/research-spike-mobilegym.md`), but an end-to-end live local run was **not**
> completed (`npm install` did not finish in 7 min; the ~1.9 GB dataset was not
> downloaded; no `bench_env.run` command executed). The architecture decision
> rests on interface contracts that are confirmed by reading source, not by
> executing a benchmark; the live run is therefore deferred to gate the
> `mobiforge[mobilegym]` extra, not Phase 0. This is why the gate verdict is
> "research/ADR complete, local-spike evidence incomplete."

The spike (`docs/research-spike-mobilegym.md`) and the three-way comparison
(`docs/runtime-concepts-comparison.md`) establish the following facts:

- MobileGym's runtime concerns — agent adapter, env backend, task spec, judge,
  trajectory — are **structurally homomorphic** to the boundaries the PRD already
  specifies. Its action set, task-as-class shape, and deterministic judge map
  cleanly onto MobiForge's planned `Action`, `VerifiableTask`, and `StateJudge`/
  `RuleJudge`. This was **confirmed by reading the actual source** of a shallow
  clone: `bench_env/env/base.py` (`BaseMobileEnv`: reset/step/get_observation/
  get_state/set_state/open_app, plus a `supports_state_injection` flag) and
  `bench_env/agent/base.py` (`BaseAgent`: act/build_messages/parse_response/reset
  + class-level `ACTION_MAP`) are structurally the PRD's `EnvBackend`/`AgentAdapter`.
- MobileGym's agent adapters are thin and already include an `autoglm` adapter
  (confirmed in `bench_env/agent/` source). This is direct evidence that the
  PRD's `OpenAutoGLMAdapter` should be a thin mapping layer, and that
  `PhoneAgent.step()` must be split rather than wrapped.
- **MobileGym's own authors already stub the real-device seam.** `bench_env/env/`
  contains `real_device.py`, `base.py` lists `RealDeviceEnv: ADB-based real device
  (TODO)`, and the `supports_state_injection` flag encodes that real-device envs
  cannot do structured-state injection. This is the strongest single piece of
  evidence that MobiForge's `AdbDeviceEnv` plugs into a seam MobileGym anticipated.
- MobileGym carries a heavy dependency surface: Node ≥ 22, Python ≥ 3.11, the
  Python `bench_env` runtime uses **Playwright Chromium**, the frontend harness
  uses **Puppeteer**, a **~1.9 GB** companion dataset, and an nginx gateway for
  large scale. (Even `npm install` alone did not finish within 7 minutes during
  the research spike.) This conflicts with PRD Story #10.
- MobileGym's code is Apache-2.0 (commercially usable), but its dataset license
  (`LICENSE-DATA`) is CC BY-NC 4.0 **and additionally** requires compliance with
  third-party platforms' developer terms; copyright in underlying user content is
  NOT transferred. "NonCommercial" is judged by purpose, so a commercial
  company's *internal* evaluation can still breach it.
- VLM judging has a ~10.2% misjudgment rate on real devices (per README),
  confirming the PRD's stance that VLM judging is a fallback, not a primary judge.

## Decision

**Adopt MobileGym's interface contract as MobiForge's own runtime contract.
MobileGym itself is an optional backend, installable as
`pip install mobiforge[mobilegym]`, and must not be required by the core
runtime.**

Concretely, this means:

1. **Action schema.** MobiForge's normalized `Action` is a subset of MobileGym's
   **18** `ActionType` values (`CLICK/DOUBLE_TAP/LONG_PRESS/TYPE/SWIPE/DRAG/BACK/
   HOME/RECENT/ENTER/WAIT/AWAKE/ANSWER/COMPLETE/ABORT/INFO/NOOP`, confirmed in
   `bench_env/env/base.py`). The set was expanded during Gate B from the PRD
   minimum of 9 to **12** types — adding `double_tap`, `long_press`, and `noop` —
   so that `OpenAutoGLMAdapter` can faithfully cover Open-AutoGLM's 14-action
   output space without losing capabilities. The full mapping (legacy `do(...)` /
   `finish(...)` → normalized `ActionType`) is in `docs/schemas.md`.
   **Coordinate normalization to `[0,1000]` is MobiForge's own design choice** —
   the actual MobileGym code uses raw pixel ints with a screen-size config, so
   this is an addition, not an inherited property.

2. **Task spec.** The `VerifiableTask` shape mirrors MobileGym's task class
   conceptually: `description` + `setup` + a goal check + optional AnswerSheet.
   But the **serialized** task spec holds **no callable**: it stores a
   `JudgeRef` (`judge_type + entrypoint + config`), and the runtime resolves that
   to a `check_goals`-like callable via a `JudgeRegistry` at run time (see
   `docs/schemas.md`). This keeps task specs persistable, replayable, and
   cross-process safe. `FreeformTask` remains a plain natural-language string for
   smoke runs.

3. **Judge.** `StateJudge` (structured state) and `RuleJudge` (action/metadata)
   are first-class and deterministic, modeled on MobileGym's `check_goals`.
   `NoneJudge` covers manual smoke runs. `VLMJudge` is an optional fallback only,
   gated behind explicit opt-in because of the measured 10.2% false-verdict rate.

4. **Agent adapter.** `OpenAutoGLMAdapter` is a thin layer (~order of 100 lines)
   modeled on MobileGym's `autoglm` adapter. It owns prompt construction, the
   model call, and parsing model output into a normalized `Action`. It does **not**
   own observation capture or action execution. `PhoneAgent.step()` is treated as
   a transitional legacy adapter and is split along the observe/act seam (see
   `docs/runtime-concepts-comparison.md` "Boundary seam" and the migration plan
   below).

5. **Env backend.** Both ADB and a MobileGym-style backend implement one
   `EnvBackend` interface (`observe / execute / optional structured state /
   health`). ADB observations carry screenshot + current_app and leave
   `env_state` absent; simulation observations populate `env_state`. The schema
   allows both without forcing fake fields (PRD Observation schema requirement).

6. **Dependency boundary.** Core `runtime/` imports must not import MobileGym.
   The MobileGym backend lives in a separate module/package loaded only when the
   `[mobilegym]` extra is installed. This is enforced by keeping the import inside
   the backend module and by a CI check that the core test suite passes with the
   extra uninstalled.

7. **License.** MobileGym **code** may be used under Apache-2.0. The MobileGym
   **dataset** (CC BY-NC 4.0) is **research-only** for MobiForge: it must not be
   redistributed, and must not be used for any commercial purpose — including a
   commercial company's internal evaluation — unless legal counsel approves or
   the data is replaced with original/re-licensed equivalents. See "License
   scope" below. This is called out here so future platform work does not
   accidentally bundle non-commercial data.

## Migration plan for `PhoneAgent.step()` (transitional, de-risks Phase 1)

The decision that `step()` must be split is firm, but the **path** to the split
is staged so the existing CLI is never broken mid-migration. Phase 1 follows
"extract first, replace second, remove third," keeping the legacy runner as a
live oracle:

1. **Extract, do not move (no behavior change).** Pull two pure helpers out of
   `PhoneAgent._execute_step()` without altering its control flow:
   - `ActionParser` — wraps `parse_action` / `finish`: model text → normalized
     `Action`. Pure function, unit-testable with fixture model outputs.
   - `ModelPlanner` — wraps prompt construction (`MessageBuilder`) + the model
     call: context + observation → raw model response. No device I/O.
   `step()` continues to call them internally. Existing tests and the CLI keep
   passing unchanged. This step alone answers PRD Open Question #3 and is the
   trunk the adapter is built on.

2. **Build the boundary behind a flag.** Introduce `EnvBackend` (with an
   `AdbDeviceEnv` that delegates to today's `device_factory` + `actions`) and
   `OpenAutoGLMAdapter` (built on `ModelPlanner` + `ActionParser`). The new
   `Runner` drives the adapter/backend pair and writes the explicit `StepResult`
   schema. Wire it behind an opt-in flag (env var or arg), defaulting to the
   legacy path.

3. **Cross-check as oracle.** Run the same task through both the legacy `step()`
   path and the new adapter/backend path. The two trajectories must agree on
   status, action sequence, and per-step observation provenance. This equivalence
   test is the safety net; mismatches are bugs to fix before flipping the
   default.

   *Status (done):* steps 1–3 are implemented and green. Step 1 extracted
   `ModelPlanner`/`ActionParser` (`phone_agent/planner.py`); step 2 built
   `OpenAutoGLMAdapter` (`runtime/adapters.py`) + `AdbDeviceEnv`
   (`runtime/env_backends.py`) + the boundary-aware `Runner`; step 3's oracle
   (`tests/test_oracle.py`) proved the two paths produce identical planner
   context on the same deterministic fakes. The oracle caught one real
   divergence during development — a missing `strip_last_image` in the adapter's
   `commit()` — now fixed. **This satisfies Gate B → schema v1 fully frozen.**

4. **Flip the default, then remove.** Once the equivalence test is green on the
   Phase 1 smoke tasks, make the adapter/backend path the default and keep the
   legacy `step()` path only behind the inverse flag for one release. Remove the
   legacy path once the two-fake-backend tracer bullet (the Phase 1 acceptance
   test) passes on the new path alone.

This plan means `step()` is never "torn apart" in one change: it is hollowed out
incrementally, with the old behavior available as a reference until the new path
is proven. Phase 1 can start immediately on step 1.

## License scope

The MobileGym dependency is **split-licensed**, and the split is load-bearing
because MobiForge's roadmap includes a platform:

- **Code: Apache-2.0.** MobileGym's code may be used, modified, and redistributed
  commercially. The `mobiforge[mobilegym]` backend adapter may depend on it.
- **Dataset and task templates (416 templates, simulated app data): CC BY-NC 4.0.**
  This is **research-only** for MobiForge. "NonCommercial" in CC BY-NC is judged
  by the *purpose*, not the actor, so a commercial company's **internal**
  evaluation can still breach it. Until legal counsel approves or the data is
  replaced with original/re-licensed equivalents, the dataset and templates:
  - must not be redistributed with any MobiForge release, and
  - must not be used for any purpose connected to a commercial product or
    internal commercial workflow.
- The `mobiforge[mobilegym]` extra must surface this clearly at install and in
  its README, and must keep the dataset download **opt-in and separate** from the
  code install.

## Consequences

### Positive

- MobiForge gets a proven adapter pattern, action set, and task/judge contract
  without inventing them. The Phase 1 two-backend milestone becomes the cheapest
  correct path: keep MobiForge's runner + trajectory store, adopt MobileGym's
  contract, split `step()`.
- The same `OpenAutoGLMAdapter` can run against both ADB and MobileGym, directly
  satisfying the first-release success criterion.
- MobiForge **may** reuse MobileGym's 416 task templates and benchmark numbers
  strictly as research references, subject to the CC BY-NC limits in "License
  scope" — not as a foundation for any commercial or internal-commercial use
  without legal sign-off or data replacement.
- The optional-extra boundary keeps the core runtime usable in deployments that
  only need real devices (PRD Story #10), and keeps upstream Open-AutoGLM
  rebases feasible (PRD Story #11).

### Negative

- Two execution paths (ADB `input tap` vs Playwright DOM click) must be kept
  coherent at the schema layer. The mitigation is the `EnvBackend` interface; the
  cost is disciplined schema ownership.
- The license split (Apache code / CC BY-NC data) must be respected forever. Any
  task-suite packaging for a commercial platform requires original or
  re-licensed data.
- Some MobileGym concepts (full structured state, AnswerSheet) only fully pay off
  on the simulation path. On real ADB they degrade to no-ops or VLM fallback, so
  real-device verifiable tasks remain harder than simulation tasks. This is
  accepted as inherent, not a bug.

### Risks accepted

- **Schema drift.** MobiForge's schemas may diverge from MobileGym's over time.
  Mitigation: a documented compatibility map and a `mobilegym` backend adapter
  that owns the translation, rather than leaking MobileGym types into the core.
- **Over-abstracting the backend boundary before enough real-device behavior is
  understood** (PRD Risk). Mitigation: Phase 2 delivers a concrete `AdbDeviceEnv`
  early, so the `EnvBackend` interface is shaped by real ADB needs, not only by
  the simulator's shape.

## Alternatives considered

### A. MobileGym as a required first-class backend

Rejected. It would bind the core runtime to Node ≥ 22, Playwright/Puppeteer, and
a ~1.9 GB dataset, directly violating PRD Story #10 and making "real-devices-only"
deployments impossible. It would also make upstream Open-AutoGLM rebases harder
(PRD Story #11) and would force every contributor to install the full simulator
stack.

### B. MobileGym as reference only — copy concepts, depend on nothing

Rejected. The spike shows MobileGym's contracts are not arbitrary — they are the
output of building 28 simulated apps and 416 tasks with deterministic judges.
Re-deriving them independently would re-spend that work (the paper reports ~60
person-days to batch-build simulated apps) and would forfeit compatibility with
the existing 416-task benchmark and the measured Sim-to-Real transfer result
(95.1% gain retained on real hardware). Adopting the contract as the contract is
strictly cheaper than re-inventing it.

### C. Wrap `PhoneAgent.step()` as-is instead of splitting it

Rejected. The comparison shows `step()` fuses perception, planning, action
parsing, and device execution into one method. The PRD's first-release milestone
requires the same adapter to run against two backends; that is impossible while
observation capture and action execution live inside the agent. This alternative
is incompatible with the milestone, independent of the MobileGym decision.

## Compliance with PRD Phase 0 acceptance criteria

- *The team can explain how one agent runs against ADB and MobileGym-style
  environments.* — Yes: one `OpenAutoGLMAdapter` against two `EnvBackend`
  implementations, documented in the boundary-seam diagram.
- *The team can identify which MobileGym concepts are adopted, adapted, or
  rejected.* — Yes: enumerated in `docs/runtime-concepts-comparison.md`.
- *No further platform features are implemented before this decision is made.* —
  Yes: this ADR is the gate; subsequent phases proceed against the contract it
  sets.

## Open items deferred to later phases

- Exact serialized schema field names and versioning — Phase 1, in
  `docs/schemas.md`, validated by the two-fake-backend tracer bullet.
- Whether a real Android accessibility tree can supplement ADB observations to
  reduce VLM reliance (PRD Open Question #5) — Phase 2, measured against `AdbDeviceEnv`.
- Concurrency, device locks, run index — explicitly out of scope until the
  Future Platform PRD (PRD "Out of Scope").
