"""Gate B Step 2c: legacy-vs-new oracle comparison.

Proves the new path (OpenAutoGLMAdapter + AdbDeviceEnv + Runner) is
final-context-equivalent to the legacy path (PhoneAgent.run() with its internal
planner) on the same task + same deterministic fakes. Context equality is the
primary oracle: both paths accumulate conversation context through the same
ModelPlanner class, so equal *final* context means message-construction +
assistant-append (image-stripped + assistant-turn) are equivalent.

Note: this asserts final-context equivalence, NOT strict step ordering. The
legacy path strips the image *before* execute; the new path strips in
``adapter.commit()`` *after* execute. The difference is immaterial to behavior
(ADB execution consumes the action dict, never the model context), so what
matters is the final context — which this harness proves equal.

Both paths run on injected fakes — no real device, no real model server. Each
path gets its own SharedFakeModel seeded with the same action script, so both
see the same response at step N. The same ProtocolCheckingHandler fake backs
both paths' execution, enforcing the real ActionHandler protocol
(``_metadata == "do"``) on both.

Completing this green => Gate B met => schema v1 fully frozen.
"""

import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from phone_agent.actions.handler import ActionResult
from phone_agent.agent import AgentConfig, PhoneAgent
from phone_agent.model.client import ModelResponse
from phone_agent.planner import ActionParser, ModelPlanner
from runtime import FreeformTask, Runner, RunStatus
from runtime.adapters import OpenAutoGLMAdapter
from runtime.env_backends import AdbDeviceEnv


# ---------------------------------------------------------------------------
# Deterministic fakes
# ---------------------------------------------------------------------------


class SharedFakeModel:
    """Returns scripted responses in order, one per request(). Each path gets
    its own instance seeded with the same script, so step N sees the same action
    on both paths regardless of call interleaving."""

    def __init__(self, script):
        self._script = list(script)
        self._i = 0

    def request(self, context):
        action = self._script[self._i]
        self._i += 1
        return ModelResponse(
            thinking=f"think-{self._i}", action=action, raw_content=f"raw-{self._i}"
        )


class FakeScreenshot:
    def __init__(self):
        self.base64_data = "IMG"
        self.width = 1080
        self.height = 2400
        self.is_sensitive = False


class FakeFactory:
    def get_screenshot(self, device_id=None, timeout=10):
        return FakeScreenshot()

    def get_current_app(self, device_id=None):
        return "Settings"


class ProtocolCheckingHandler:
    """Enforces the real ActionHandler.execute() protocol on both paths:

    - ``_metadata == "do"``    -> ActionResult(True, False)  (executable, success)
    - ``_metadata == "finish"``-> ActionResult(True, True, message)  (legacy still
      calls execute() on finish; the new Runner does NOT, so a finish dict
      reaching this handler on the new path is a bug)
    - anything else            -> Unknown-action failure (catches a missing
      ``_metadata`` regression in ``AdbDeviceEnv._reverse_map``).

    Records every call so tests can assert what each path executed.
    """

    def __init__(self):
        self.calls = []  # list of action_dict
        self.saw_finish = False

    def execute(self, action_dict, width, height):
        self.calls.append(action_dict)
        metadata = action_dict.get("_metadata")
        if metadata == "do":
            return ActionResult(success=True, should_finish=False)
        if metadata == "finish":
            self.saw_finish = True
            return ActionResult(success=True, should_finish=True, message=action_dict.get("message"))
        return ActionResult(
            success=False, should_finish=True, message=f"Unknown action type: {metadata}"
        )


# ---------------------------------------------------------------------------
# Path builders
# ---------------------------------------------------------------------------


def _config(max_steps=100):
    return AgentConfig(max_steps=max_steps, lang="cn", verbose=False)


def _run_legacy(script, task, max_steps=100):
    """Drive the legacy PhoneAgent.run() path; return (final_message, context, handler)."""
    agent = PhoneAgent(agent_config=_config(max_steps))
    # Swap in the deterministic model on both the agent and its planner.
    fake_model = SharedFakeModel(script)
    agent.model_client = fake_model
    agent.planner.model_client = fake_model
    # Replace the action handler with the shared protocol-checking fake so no real
    # device/keyboard logic runs (and the _metadata protocol is enforced).
    handler = ProtocolCheckingHandler()
    agent.action_handler = handler

    with patch("phone_agent.agent.get_device_factory", return_value=FakeFactory()):
        with redirect_stdout(StringIO()):  # suppress any residual prints
            message = agent.run(task)

    return message, agent.planner.context_copy(), handler


def _run_new(script, task, max_steps=100):
    """Drive the new Runner path; return (run_result, context, handler)."""
    fake_model = SharedFakeModel(script)
    planner = ModelPlanner(fake_model, system_prompt=_config().system_prompt, lang="cn", verbose=False)
    parser = ActionParser(verbose=False, lang="cn")
    adapter = OpenAutoGLMAdapter(planner, parser)
    handler = ProtocolCheckingHandler()
    backend = AdbDeviceEnv(device_factory=FakeFactory(), action_handler=handler)

    with redirect_stdout(StringIO()):
        result = Runner(adapter, backend).run(FreeformTask(task, max_steps=max_steps))

    return result, adapter.planner.context_copy(), handler


# ---------------------------------------------------------------------------
# Context-equality helper (prints a diff on mismatch)
# ---------------------------------------------------------------------------


def _assert_contexts_equal(testcase, legacy_ctx, new_ctx, script):
    if legacy_ctx == new_ctx:
        return
    # Diagnostic: find the first differing message and report it.
    diff_lines = [
        "",
        "==== CONTEXT DIVERGENCE (oracle) ====",
        f"script: {script}",
        f"legacy len={len(legacy_ctx)}  new len={len(new_ctx)}",
    ]
    for i in range(max(len(legacy_ctx), len(new_ctx))):
        l = legacy_ctx[i] if i < len(legacy_ctx) else "<missing>"
        n = new_ctx[i] if i < len(new_ctx) else "<missing>"
        if l != n:
            diff_lines.append(f"first diff at index {i}:")
            diff_lines.append(f"  legacy: {l}")
            diff_lines.append(f"  new:    {n}")
            break
    diff_lines.append("=====================================")
    testcase.fail("\n".join(diff_lines))


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class OracleEquivalenceTests(unittest.TestCase):
    TASK = "打开设置"

    def test_finish_in_two_steps_context_equivalent(self):
        script = ['do(action="Tap", element=[500,500])', 'finish(message="done")']

        legacy_msg, legacy_ctx, legacy_handler = _run_legacy(script, self.TASK, max_steps=5)
        new_result, new_ctx, new_handler = _run_new(script, self.TASK, max_steps=5)

        # Primary oracle: contexts equal.
        _assert_contexts_equal(self, legacy_ctx, new_ctx, script)

        # Secondary: both finished successfully.
        self.assertIn("done", legacy_msg)
        self.assertEqual(new_result.status, RunStatus.SUCCEEDED)

        # Protocol guard: both paths executed Tap with _metadata=="do".
        legacy_execs = [d for d in legacy_handler.calls if d.get("_metadata") == "do"]
        new_execs = [d for d in new_handler.calls if d.get("_metadata") == "do"]
        self.assertEqual(legacy_execs, new_execs)
        self.assertTrue(all(d["_metadata"] == "do" for d in new_execs))

        # New-path-never-finishes guard: the Runner short-circuits FINISH, so the
        # new handler must NOT have seen a finish dict.
        self.assertFalse(new_handler.saw_finish,
                         "new path executed finish — Runner did not short-circuit FINISH")
        # (Legacy DOES call execute on finish, by design.)

    def test_multistep_context_equivalent(self):
        script = [
            'do(action="Tap", element=[100,100])',
            'do(action="Swipe", start=[500,800], end=[500,100])',
            'do(action="Type", text="hello")',
            'finish(message="all done")',
        ]

        legacy_msg, legacy_ctx, _ = _run_legacy(script, self.TASK, max_steps=10)
        new_result, new_ctx, _ = _run_new(script, self.TASK, max_steps=10)

        _assert_contexts_equal(self, legacy_ctx, new_ctx, script)
        self.assertIn("all done", legacy_msg)
        self.assertEqual(new_result.status, RunStatus.SUCCEEDED)

    def test_max_steps_context_equivalent_with_exact_length(self):
        # Never finishes; both paths exhaust max_steps=2 with 2 executed Taps.
        script = ['do(action="Tap", element=[1,1])'] * 3

        legacy_msg, legacy_ctx, _ = _run_legacy(script, self.TASK, max_steps=2)
        new_result, new_ctx, _ = _run_new(script, self.TASK, max_steps=2)

        # Off-by-one guard: for N=2 executed non-finish steps, context length
        # must be exactly 2*N + 1 = 5 (system + first user + 2 assistants + 1
        # follow-up user). A length mismatch fails loud.
        self.assertEqual(len(legacy_ctx), 5, f"legacy ctx len wrong: {len(legacy_ctx)}")
        self.assertEqual(len(new_ctx), 5, f"new ctx len wrong: {len(new_ctx)}")
        _assert_contexts_equal(self, legacy_ctx, new_ctx, script)

        # Both hit the step limit.
        self.assertEqual(legacy_msg, "Max steps reached")
        self.assertEqual(new_result.status, RunStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
