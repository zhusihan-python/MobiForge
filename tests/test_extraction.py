"""Gate B Step 1 oracle: the extraction from PhoneAgent._execute_step() into
ModelPlanner + ActionParser preserves public API and observable behavior.

There is no real device, so equivalence is proven against the inline code with
stubs. These tests are the oracle that the extraction did not drift. Style
mirrors the other test modules: stdlib unittest, no extra deps beyond what
phone_agent already needs (openai/pillow/imageio).

Locked step order (must not reorder):
    observe -> planner.plan -> parser.parse -> planner.strip_last_image
      -> action_handler.execute -> planner.append_assistant -> finished
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from phone_agent.actions.handler import ActionResult
from phone_agent.agent import AgentConfig, PhoneAgent
from phone_agent.model.client import MessageBuilder, ModelResponse
from phone_agent.planner import ActionParser, ModelPlanner


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class FakeModelClient:
    """Stand-in for ModelClient. Returns a canned response or raises."""

    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.received_context = None

    def request(self, context):
        self.received_context = context
        if self._raises is not None:
            raise self._raises
        return self._response


def _canned_response(action_text='do(action="Tap", element=[500,500])'):
    return ModelResponse(
        thinking="let me tap", action=action_text, raw_content="raw"
    )


class _FakeScreenshot:
    """Matches adb.Screenshot's attribute surface used by _execute_step."""

    def __init__(self, base64_data="IMG", width=1080, height=2400, is_sensitive=False):
        self.base64_data = base64_data
        self.width = width
        self.height = height
        self.is_sensitive = is_sensitive


class _FakeFactory:
    def __init__(self, screenshot=None, current_app="Settings"):
        self._screenshot = screenshot or _FakeScreenshot()
        self._current_app = current_app

    def get_screenshot(self, device_id=None, timeout=10):
        return self._screenshot

    def get_current_app(self, device_id=None):
        return self._current_app


def _patched_device():
    """patch() context that replaces get_device_factory in phone_agent.agent."""
    return patch(
        "phone_agent.agent.get_device_factory",
        return_value=_FakeFactory(),
    )


# ---------------------------------------------------------------------------
# 1. Parse equivalence
# ---------------------------------------------------------------------------


class ActionParserTests(unittest.TestCase):
    def test_parses_do_action_into_exact_dict(self):
        parser = ActionParser(verbose=False)
        action = parser.parse('do(action="Tap", element=[500, 500])')
        self.assertEqual(
            action,
            {"_metadata": "do", "action": "Tap", "element": [500, 500]},
        )

    def test_finish_fallback_on_unparseable_input(self):
        parser = ActionParser(verbose=False)
        # Garbage that parse_action rejects -> finish fallback carrying the input.
        action = parser.parse("totally not an action")
        self.assertEqual(action["_metadata"], "finish")
        self.assertEqual(action["message"], "totally not an action")

    def test_verbose_fallback_prints_traceback(self):
        """Regression (review P1): the ValueError fallback must print the
        traceback when verbose=True, exactly as the inline code did. Captured
        via stderr (traceback.print_exc writes to stderr by default)."""
        import sys

        parser = ActionParser(verbose=True)
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            parser.parse("totally not an action")
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        # A traceback contains the exception type and the originating frame.
        self.assertIn("ValueError", err)
        self.assertIn("parse_action", err)

    def test_quiet_fallback_prints_no_traceback(self):
        import sys

        parser = ActionParser(verbose=False)
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            parser.parse("totally not an action")
            err = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        self.assertEqual(err, "")


# ---------------------------------------------------------------------------
# 2. Plan / context equivalence
# ---------------------------------------------------------------------------


class ModelPlannerPlanTests(unittest.TestCase):
    def _make_planner(self, fake_client):
        return ModelPlanner(
            model_client=fake_client,
            system_prompt="SYS",
            lang="cn",
            verbose=False,
        )

    def test_first_step_builds_system_then_user_with_image(self):
        fake = FakeModelClient(_canned_response())
        planner = self._make_planner(fake)

        planner.plan(
            screenshot_base64="IMG",
            current_app="Settings",
            user_prompt="打开设置",
            is_first=True,
        )

        # The context the model received must equal what the inline code built.
        ctx = planner.context_copy()
        self.assertEqual(len(ctx), 2)
        self.assertEqual(ctx[0], MessageBuilder.create_system_message("SYS"))
        self.assertEqual(
            ctx[1],
            MessageBuilder.create_user_message(
                text="打开设置\n\n" + MessageBuilder.build_screen_info("Settings"),
                image_base64="IMG",
            ),
        )

    def test_followup_step_builds_screen_info_only_message(self):
        fake = FakeModelClient(_canned_response())
        planner = self._make_planner(fake)

        planner.plan(
            screenshot_base64="IMG1",
            current_app="Settings",
            user_prompt="打开设置",
            is_first=True,
        )
        planner.plan(
            screenshot_base64="IMG2", current_app="Settings", is_first=False
        )

        ctx = planner.context_copy()
        # 1st step: system + user ; 2nd step: one user (screen-info only).
        self.assertEqual(len(ctx), 3)
        expected_text = "** Screen Info **\n\n" + MessageBuilder.build_screen_info(
            "Settings"
        )
        self.assertEqual(
            ctx[2],
            MessageBuilder.create_user_message(text=expected_text, image_base64="IMG2"),
        )


# ---------------------------------------------------------------------------
# 3. Order locked
# ---------------------------------------------------------------------------


class StepOrderTests(unittest.TestCase):
    def test_strip_image_before_execute_and_append_after(self):
        """Verify the locked order via a PhoneAgent whose backend/handler are
        stubbed, observing the sequence of planner/handler calls."""
        agent = _build_stubbed_agent(_canned_response())

        # Record the order of: planner.strip_last_image, action_handler.execute,
        # planner.append_assistant.
        order = []
        agent.planner.strip_last_image = lambda: order.append("strip")
        agent.planner.append_assistant = lambda t, a: order.append("append")
        orig_execute = agent.action_handler.execute

        def spy_execute(action, w, h):
            order.append("execute")
            return orig_execute(action, w, h)

        agent.action_handler.execute = spy_execute

        with _patched_device():
            agent._execute_step("打开设置", is_first=True)

        # strip must come BEFORE execute; append must come AFTER execute.
        self.assertEqual(order, ["strip", "execute", "append"])


def _build_stubbed_agent(canned_response):
    """Build a PhoneAgent without touching the real model or device."""
    agent = PhoneAgent.__new__(PhoneAgent)
    agent.model_config = None
    agent.agent_config = AgentConfig(verbose=False)
    agent.model_client = FakeModelClient(canned_response)
    from phone_agent.actions.handler import ActionHandler

    agent.action_handler = ActionHandler.__new__(ActionHandler)
    agent.action_handler.execute = lambda action, w, h: ActionResult(
        success=True, should_finish=False, message=None
    )
    agent.planner = ModelPlanner(
        model_client=agent.model_client,
        system_prompt="SYS",
        lang="cn",
        verbose=False,
    )
    agent.parser = ActionParser(verbose=False, lang="cn")
    agent._step_count = 0
    return agent


# ---------------------------------------------------------------------------
# 4. Public surface (reset / context copy / first-step detection)
# ---------------------------------------------------------------------------


class PublicSurfaceTests(unittest.TestCase):
    def test_reset_clears_planner_context_and_step_count(self):
        agent = _build_stubbed_agent(_canned_response())
        agent.planner._context.append({"x": 1})
        agent._step_count = 5

        agent.reset()

        self.assertEqual(agent.planner.context_len(), 0)
        self.assertEqual(agent._step_count, 0)

    def test_context_returns_shallow_copy(self):
        agent = _build_stubbed_agent(_canned_response())
        agent.planner._context.append({"role": "system", "content": "S"})

        snapshot = agent.context
        snapshot.append({"role": "user", "content": "injected"})

        # Mutating the returned list must not affect the planner's context.
        self.assertEqual(agent.planner.context_len(), 1)

    def test_step_first_step_detection_uses_planner_context_len(self):
        agent = _build_stubbed_agent(_canned_response())

        # Empty planner context -> first step; no task -> ValueError (legacy
        # behavior preserved). step() checks context BEFORE touching the device.
        with self.assertRaises(ValueError):
            agent.step()


# ---------------------------------------------------------------------------
# 5. Print side-effects preserved
# ---------------------------------------------------------------------------


class PrintSideEffectTests(unittest.TestCase):
    def test_plan_prints_thinking_header(self):
        fake = FakeModelClient(_canned_response())
        planner = ModelPlanner(fake, "SYS", "cn", verbose=True)

        buf = io.StringIO()
        with redirect_stdout(buf):
            planner.plan(
                screenshot_base64="IMG", current_app="Settings", is_first=True
            )

        out = buf.getvalue()
        self.assertIn("思考过程", out)  # thinking header (cn locale)
        self.assertIn("====", out)

    def test_parse_prints_action_dump_when_verbose(self):
        parser = ActionParser(verbose=True, lang="cn")
        buf = io.StringIO()
        with redirect_stdout(buf):
            parser.parse('do(action="Tap", element=[500, 500])')

        out = buf.getvalue()
        self.assertIn("执行动作", out)  # action header (cn locale)
        self.assertIn("Tap", out)

    def test_parse_verbose_dump_is_gated_by_verbose(self):
        """Our verbose action dump (the 🎯 block) is gated by verbose. Note the
        underlying parse_action() always prints a 'Parsing action:' debug line —
        that is pre-existing behavior our extraction preserves faithfully; we
        only assert OUR dump is absent when verbose=False."""
        parser_quiet = ActionParser(verbose=False, lang="cn")
        buf = io.StringIO()
        with redirect_stdout(buf):
            parser_quiet.parse('do(action="Tap", element=[500, 500])')
        self.assertNotIn("执行动作", buf.getvalue())  # our dump absent
        self.assertNotIn("🎯", buf.getvalue())

        parser_loud = ActionParser(verbose=True, lang="cn")
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            parser_loud.parse('do(action="Tap", element=[500, 500])')
        self.assertIn("执行动作", buf2.getvalue())  # our dump present


# ---------------------------------------------------------------------------
# 6. Model-error path stays in PhoneAgent
# ---------------------------------------------------------------------------


class ModelErrorTests(unittest.TestCase):
    def test_model_error_propagates_from_plan_and_phoneagent_handles_it(self):
        """plan() must raise (not swallow); PhoneAgent's try/except returns the
        failure StepResult. No sentinel type is introduced."""
        fake = FakeModelClient(raises=RuntimeError("model down"))
        planner = ModelPlanner(fake, "SYS", "cn", verbose=False)

        # plan() propagates.
        with self.assertRaises(RuntimeError):
            planner.plan(
                screenshot_base64="IMG", current_app="Settings", is_first=True
            )

        # And PhoneAgent converts it into a failure StepResult.
        agent = _build_stubbed_agent(None)
        agent.model_client = fake
        agent.planner = ModelPlanner(fake, "SYS", "cn", verbose=False)

        with _patched_device():
            result = agent._execute_step("打开设置", is_first=True)

        self.assertFalse(result.success)
        self.assertTrue(result.finished)
        self.assertEqual(result.action, None)
        self.assertIn("Model error: model down", result.message)


if __name__ == "__main__":
    unittest.main()
