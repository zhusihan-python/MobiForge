# Research Spike Notes: MobileGym

> Phase 0 deliverable. **Research** spike on MobileGym before the Phase 0 ADR.
>
> **This is a research spike, not a local run.** Findings come from reading the
> paper (arXiv 2605.26114v1), the official GitHub repo
> (github.com/Purewhiter/mobilegym), mobilegym.dev, and — crucially — reading the
> actual source code of a shallow clone. A live end-to-end local run was
> attempted but not completed (see `Local Run Evidence`).
>
> **Provenance legend** used on every hard claim below:
> - **[SRC]** = read directly in cloned source code (highest confidence)
> - **[README]** = read in the repo README (high)
> - **[PAPER]** = read in arXiv 2605.26114v1 (high for paper-specific claims)
> - **[SUMMARY]** = from search/web summaries only, NOT independently verified
>   (directional; treat as unconfirmed until checked)

## Local Run Evidence

A shallow clone was performed (`git clone --depth 1`, succeeded) and the source
code was read directly (see `[SRC]`-tagged findings). An end-to-end local run
was **not** completed:

- `npm install` (frontend) was started but did not finish within a 7-minute
  timeout; `node_modules/` was not created. This is a real signal about frontend
  install weight, not a setup error.
- The companion dataset (~1.9 GB per README, not ~1.4 GB) was not downloaded.
- `bench_env` Python deps and `playwright install chromium` were not installed;
  no `bench_env.run` command was executed.

Environment probed: Node v25.9.0 (≥22 ✓), Python 3.14.6 (≥3.11 ✓), 1.4 TB free
disk. So the runtime *can* host MobileGym locally; the gap was time, not
feasibility.

**Recommendation:** a true local run (boot simulator → `--agent human` on one
task → `--list`) should be the gate for the `mobiforge[mobilegym]` extra in a
later phase, not a blocker for the Phase 0 *architecture* decision — which rests
on interface contracts that are confirmed by reading source, not by executing a
benchmark. This honest gap is why the gate verdict is "research/ADR complete,
local-spike evidence incomplete."

## What MobileGym Is

A **browser-hosted lightweight Android simulator** built for evaluating and training
mobile GUI agents. The entire environment state is modeled as **structured JSON**,
which makes state:

- **readable** — for deterministic judging
- **writable** — for reset and setup injection
- **forkable** — for parallel rollout
- **diffable** — for side-effect detection

| Dimension | Value | Provenance |
|---|---|---|
| Simulated apps | 28 (12 daily apps + 16 system apps) | [README] highlights |
| Task templates | 416 (256 test + 160 train), parameterized to >27K instances | [README] / [SUMMARY]; **[SRC]** `bench_env/task/` contains 28 app dirs + `registry.py`/`sampler.py` |
| Memory per instance | ~400 MB | [README] |
| Parallelism | 256 instances on one server, <10% CPU | [README] |
| Cold start | ~3 s | [README] |
| Full 256-task evaluation | ~6 min | [README] |
| Companion dataset size | ~1.9 GB | [README] (corrected: was "~1.4 GB" in earlier draft) |

**Implementation stack (corrected from first-hand reading):**

- Simulator front end: React 19 + Vite + Zustand, built assets served by
  `vite preview` or an nginx gateway for heavy parallelism. **[SRC]**
- Agent → simulator bridge: **two** separate tools, not one:
  - **Puppeteer** (`puppeteer` in `package.json` devDeps) — used by the
    frontend/harness. **[SRC]**
  - **Playwright Chromium** (`playwright>=1.40` in `bench_env/requirements.txt`)
    — used by the Python `bench_env` evaluation runtime. **[SRC]**
- Python orchestration: `bench_env/` (the part MobiForge actually cares about).

> Correction to earlier draft: "Playwright as the bridge" was half-right. The
> Python eval runtime (`bench_env`) does use Playwright; the frontend harness
> uses Puppeteer. ADR tension #2 should say "Playwright/Puppeteer", not just
> "Playwright".

## The Five Concepts (mapping to MobiForge boundaries) — verified against source

These five MobileGym concepts are **structurally homomorphic** to the boundaries
the PRD calls for. This is the central finding, and it is now **confirmed by
reading the actual `bench_env/env/base.py` and `bench_env/agent/base.py` source**,
not just the paper.

| MobileGym concept | How it is realized **[SRC]** | MobiForge target | Fit |
|---|---|---|---|
| Structured env state | `BaseMobileEnv.get_state()` / `set_state()` operate on JSON dicts; `Observation.state` carries it. | `EnvBackend` + `Observation.env_state` | exact |
| Action abstraction | `ActionType` enum, **18 actions** (see below), coords as raw pixel ints (NOT normalized in this code). | `Action` schema | near-exact (normalization is MobiForge's addition) |
| Deterministic judge | `bench_env/task/judge.py` + per-task judge; `vlm_judge.py` is the fallback. | `StateJudge` + `RuleJudge` | exact |
| Task as a Python class | `bench_env/task/base.py` `BaseTask` + `bench_env/task/registry.py`. | `VerifiableTask` | exact |
| AnswerSheet protocol | `ActionType.ANSWER` action + `BaseMobileEnv.agent_answer`. | TaskSpec design reference | exact |

**Correction on the action count:** the source defines **18 `ActionType`
values**, not 17: `CLICK, DOUBLE_TAP, LONG_PRESS, TYPE, SWIPE, DRAG, BACK, HOME,
RECENT, ENTER, WAIT, AWAKE, ANSWER, COMPLETE, ABORT, INFO, NOOP`
(`bench_env/env/base.py`). **[SRC]**

**Correction on coordinate normalization:** the actual `Action` data uses **raw
pixel integers** (e.g. `{"point": [x, y]}`), with screen size coming from
`AgentConfig.screen_size` / `get_device_size()` (default 1080×2400). There is no
`[0,1000]` normalization in the code I read. So `[0,1000]` normalization is
**MobiForge's design choice**, not an inherited MobileGym property — the earlier
draft stated it as if MobileGym already did this. **[SRC]**

## Agent Adapter Layer (directly answers PRD Open Question #3) — confirmed [SRC]

`bench_env/agent/` ships these adapters: `autoglm, gelab, generic, generic_v2,
gui_owl, human, mai_ui, uitars, venus` (9 total). **`autoglm.py` exists** — this
is the direct template for MobiForge's `OpenAutoGLMAdapter`. **[SRC]**

`bench_env/agent/base.py` defines `BaseAgent` with exactly the seam the PRD wants:
`act(obs) -> Action` (abstract), `build_messages(obs)`, `parse_response(text)`,
`reset(task)`, plus a class-level `ACTION_MAP` mapping model-specific action names
to `(ActionType, data_fn)`. **[SRC]** This is strong first-hand evidence that the
target `OpenAutoGLMAdapter` should be a thin mapping layer — prompt + model call +
parse — **not** a `PhoneAgent.step()` monolith. Observation capture and action
execution live on the *env*, not the *agent*.

## A key find: MobileGym already stubs the real-device env [SRC]

`bench_env/env/` contains `mobile_gym.py` (sim) **and `real_device.py`**, and
`base.py`'s `BaseMobileEnv` docstring lists `RealDeviceEnv: ADB-based real device
(TODO)`. Crucially, the base interface carries a **`supports_state_injection:
bool = True`** class flag, with the comment: *"Sim envs support this; real-device
envs (screenshot + ADB only) do not, which means grounded-mode answer_sheet
injection must be skipped."*

> This is the single strongest piece of evidence for the ADR: MobileGym's own
> authors already designed the boundary so a real-device env can plug in and
> gracefully drop structured-state features. MobiForge's `AdbDeviceEnv` slots
> into exactly this opening. This resolves PRD Risk "MobileGym schemas may not
> map cleanly to real ADB" in the design's favor.

## Hard Data Points (informs the ADR) — with provenance

1. **VLM judge ~10.2% misjudgment rate.** [README] "Why MobileGym?" table states
   this explicitly and attributes it to VLM judging on real devices. [PAPER]
   reportedly has the full audit; **not independently verified at section/table
   granularity** → confidence high on the number, medium on exact provenance.
2. **Sim-to-Real retains 95.1% of sim training gain (+40.7 pt real).** [README]
   TL;DR and "Sim-to-Real" section, on a Redmi Note 12 Turbo. Real device: Qwen3-
   VL-4B + GRPO.
3. **AutoGLM-Phone-9B SR on MobileGym.** [SUMMARY] earlier said "SR=20.0%";
   [README] leaderboard section exists but I did not read the exact number per
   model — **treat 20.0% as unconfirmed; verify against the leaderboard table
   before citing.** (The README news notes Gemini 3.1 Pro tops at 58.8%. [README])
4. **Unsafe Side Effect (USE) ranges ~4.7%–14.5% across 9 models.** [SUMMARY]
   only — **NOT independently verified.** Directional; confirm in the paper's
   results tables before relying on the exact range.
5. **`openai` pinned `<2.25`** in `bench_env/requirements.txt` because newer
   versions reject the empty api_key used for local/gateway endpoints. **[SRC]**
   — operationally important for anyone running `bench_env`.

## Provisional Answers to PRD Open Questions

| PRD OQ | Spike-informed provisional answer |
|---|---|
| #1 MobileGym positioning | Optional backend. Its interface contract (`BaseMobileEnv`/`BaseAgent`) — confirmed in source — should be adopted as MobiForge's own contract. |
| #2 Adopt task schema? | Adopt a compatibility layer. `BaseTask` + registry maps to `VerifiableTask` + `JudgeRegistry`; AnswerSheet (`ANSWER` action) is worth absorbing. |
| #3 PhoneAgent.step()? | Split into a thin adapter modeled on `bench_env/agent/autoglm.py`. `act/build_messages/parse_response` is the seam. Do not keep the monolith. |
| #4 Minimum normalized action? | Adopt a subset of the 18 `ActionType`s. PRD min-set (`tap/swipe/type_text/home/back/launch_app/wait/finish/request_user`) maps to `CLICK/SWIPE/TYPE/HOME/BACK/AWAKE/WAIT/COMPLETE/INFO`. Note: normalization to `[0,1000]` is **MobiForge's choice**, not inherited. |
| #5 Structured state from real Android? | Very little — and MobileGym's `supports_state_injection` flag already encodes this: real-device envs can't do it. Real observations are screenshot + current_app + (optional) a11y tree. |
| #7 Trajectory optimization? | Human debugging first. Paper's Sim-to-Real diagnosis (reportedly Listing 1 [SUMMARY]) was found via step-level think traces. |

## Tensions the ADR Must Resolve (corrected)

1. **Dependency weight.** [README] Install requires Node ≥ 22, Python ≥ 3.11,
   `pip install -r bench_env/requirements.txt`, `playwright install chromium`,
   and a **~1.9 GB** dataset download. Confirmed empirically: even `npm install`
   alone did not finish in 7 min. This conflicts with PRD Story #10. **Resolution:
   optional extra `pip install mobiforge[mobilegym]`; core runtime imports must
   not depend on it; dataset download opt-in and separate.**
2. **Architecture-language mismatch.** MobileGym is a React/TS simulator; the
   Python `bench_env` drives it via **Playwright** (frontend harness uses
   **Puppeteer**). MobiForge is pure Python over ADB. The `Action`/`Observation`
   concepts are isomorphic, but execution paths differ fundamentally (Playwright
   DOM clicks vs `adb shell input tap`). **Resolution: unify at the runtime
   schema layer, not the execution layer — `EnvBackend` abstracts this.** The
   `supports_state_injection` flag shows the authors already planned for this
   seam. [SRC]
3. **License — stricter than first stated.** [SRC] `LICENSE-DATA` is CC BY-NC 4.0
   **and additionally** requires compliance with third-party platforms' developer
   terms; copyright in underlying user content is **NOT** transferred. So
   "non-commercial" is judged by purpose; a commercial company's *internal* eval
   can still breach it. See ADR "License scope."

## Decision Chosen

After this spike, the chosen positioning (recorded in
`docs/adr/0001-mobilegym-positioning.md`):

> **Adopt MobileGym's interface contract as MobiForge's own runtime contract;
> MobileGym itself is an optional backend (`mobiforge[mobilegym]`).**

Rationale, now first-hand-confirmed: the `BaseMobileEnv`/`BaseAgent` contracts in
source are structurally homomorphic to the PRD boundaries, the `autoglm` adapter
is a real template for `OpenAutoGLMAdapter`, MobileGym's own `supports_state_injection`
flag proves the real-device seam is anticipated, and an optional dependency
honors PRD Story #10. The three tensions above are all addressable without
compromising the core runtime's independence.

## What remains unverified (deferred to a true local run)

- Exact per-model leaderboard numbers (AutoGLM-Phone-9B SR, etc.) — read the
  README leaderboard table directly before citing in any public artifact.
- The USE 4.7%–14.5% range — confirm in the paper's results tables.
- A live `--agent human` task run and a `--list` output — the empirical proof
  that the install + dataset path works end to end. Gate this on the
  `mobiforge[mobilegym]` extra, not on Phase 0 architecture.
