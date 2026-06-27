"""Gate B Step 2a: OpenAutoGLMAdapter maps the 14-action model output space to
the 12 normalized ActionTypes, composing ModelPlanner + ActionParser. Tested
with fake observations only — no device, no model server.

Covers: per-action mapping, raw provenance, first-vs-followup step, coordinate
pass-through, Wait duration parsing, unknown-action failure, reset, and model-
error propagation.
"""

import unittest
from datetime import datetime, timezone

from phone_agent.model.client import ModelResponse
from phone_agent.planner import ActionParser, ModelPlanner
from runtime.adapters import OpenAutoGLMAdapter
from runtime.schemas import ActionType, Observation


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class FakeModelClient:
    """Returns a canned response (set its .action to a do(...)/finish(...) string),
    or raises if ._raises is set."""

    def __init__(self, action_text='do(action="Tap", element=[500,500])', raises=None):
        self._action_text = action_text
        self._raises = raises

    def request(self, context):
        if self._raises is not None:
            raise self._raises
        return ModelResponse(thinking="t", action=self._action_text, raw_content="rc")


def _observation(screenshot="IMG", current_app="Settings"):
    return Observation(
        id="obs-1",
        timestamp=datetime.now(timezone.utc).isoformat(),
        screenshot=screenshot,
        current_app=current_app,
    )


def _build_adapter(action_text='do(action="Tap", element=[500,500])', verbose=False):
    """Build an adapter wired to a FakeModelClient returning the given action."""
    fake = FakeModelClient(action_text=action_text)
    planner = ModelPlanner(fake, system_prompt="SYS", lang="cn", verbose=verbose)
    parser = ActionParser(verbose=verbose, lang="cn")
    adapter = OpenAutoGLMAdapter(planner, parser)
    adapter.reset("打开设置")
    return adapter, fake


# ---------------------------------------------------------------------------
# 1. Each legacy action maps correctly
# ---------------------------------------------------------------------------


class ActionMappingTests(unittest.TestCase):
    CASES = [
        # (model output string, expected ActionType, field-check callable)
        ('do(action="Tap", element=[120, 340])', ActionType.TAP,
         lambda a: a.coordinates == [120.0, 340.0]),
        ('do(action="Double Tap", element=[10, 20])', ActionType.DOUBLE_TAP,
         lambda a: a.coordinates == [10.0, 20.0]),
        ('do(action="Long Press", element=[990, 5])', ActionType.LONG_PRESS,
         lambda a: a.coordinates == [990.0, 5.0]),
        ('do(action="Type", text="hello")', ActionType.TYPE_TEXT,
         lambda a: a.text == "hello"),
        ('do(action="Type_Name", text="Alice")', ActionType.TYPE_TEXT,
         lambda a: a.text == "Alice"),
        ('do(action="Swipe", start=[100, 800], end=[100, 100])', ActionType.SWIPE,
         lambda a: a.coordinates == [100.0, 800.0, 100.0, 100.0]),
        ('do(action="Back")', ActionType.BACK, lambda a: True),
        ('do(action="Home")', ActionType.HOME, lambda a: True),
        ('do(action="Launch", app="WeChat")', ActionType.LAUNCH_APP,
         lambda a: a.app == "WeChat"),
        ('do(action="Wait", duration="3 seconds")', ActionType.WAIT,
         lambda a: a.duration_ms == 3000),
        ('do(action="Take_over", message="need login help")', ActionType.REQUEST_USER,
         lambda a: a.text == "need login help"),
        ('do(action="Interact")', ActionType.REQUEST_USER,
         lambda a: a.text == "User interaction required"),
        ('do(action="Note", message="True")', ActionType.NOOP, lambda a: True),
        ('do(action="Call_API", instruction="summarize")', ActionType.NOOP,
         lambda a: True),
        ('finish(message="all done")', ActionType.FINISH,
         lambda a: a.text == "all done"),
    ]

    def test_each_legacy_action_maps_to_expected_type_and_fields(self):
        for action_text, expected_type, field_ok in self.CASES:
            with self.subTest(action=action_text):
                adapter, _ = _build_adapter(action_text)
                action = adapter.act(_observation())
                self.assertEqual(action.type, expected_type, msg=action_text)
                self.assertTrue(field_ok(action), msg=f"fields wrong for {action_text}")


# ---------------------------------------------------------------------------
# 2. raw provenance
# ---------------------------------------------------------------------------


class RawProvenanceTests(unittest.TestCase):
    def test_every_action_carries_original_dict_in_raw(self):
        for action_text, _, _ in ActionMappingTests.CASES:
            with self.subTest(action=action_text):
                adapter, _ = _build_adapter(action_text)
                action = adapter.act(_observation())
                self.assertIsInstance(action.raw, dict)
                # The raw dict carries the metadata discriminator.
                self.assertIn(action.raw.get("_metadata"), ("do", "finish"))


# ---------------------------------------------------------------------------
# 3. First vs follow-up step
# ---------------------------------------------------------------------------


class FirstStepTests(unittest.TestCase):
    def test_first_step_builds_system_plus_user_second_step_screen_info_only(self):
        adapter, _ = _build_adapter('do(action="Tap", element=[1,1])')
        adapter.act(_observation())  # first step

        ctx_after_first = adapter.planner.context_copy()
        self.assertEqual(len(ctx_after_first), 2)  # system + user

        adapter.act(_observation())  # follow-up
        ctx_after_second = adapter.planner.context_copy()
        # The adapter only plans + parses; it does NOT append the assistant turn
        # (that is the Runner's job). So after step 2 the planner context is
        # system + user(first) + user(screen-info) = 3.
        self.assertEqual(len(ctx_after_second), 3)


# ---------------------------------------------------------------------------
# 4. Coordinates pass through (no transformation)
# ---------------------------------------------------------------------------


class CoordinateTests(unittest.TestCase):
    def test_coordinates_pass_through_unmodified(self):
        adapter, _ = _build_adapter('do(action="Tap", element=[500, 500])')
        action = adapter.act(_observation())
        # [0,1000] native model output; backend converts to pixels later.
        self.assertEqual(action.coordinates, [500.0, 500.0])


# ---------------------------------------------------------------------------
# 5. Wait duration parsing
# ---------------------------------------------------------------------------


class WaitParsingTests(unittest.TestCase):
    def test_duration_string_parsed_to_ms(self):
        adapter, _ = _build_adapter('do(action="Wait", duration="5 seconds")')
        action = adapter.act(_observation())
        self.assertEqual(action.duration_ms, 5000)

    def test_duration_missing_yields_none(self):
        adapter, _ = _build_adapter('do(action="Wait")')
        action = adapter.act(_observation())
        self.assertIsNone(action.duration_ms)


# ---------------------------------------------------------------------------
# 6. Unknown action fails loud
# ---------------------------------------------------------------------------


class UnknownActionTests(unittest.TestCase):
    def test_unknown_action_name_raises(self):
        adapter, _ = _build_adapter('do(action="Bogus", element=[1,1])')
        with self.assertRaises(ValueError) as ctx:
            adapter.act(_observation())
        self.assertIn("Bogus", str(ctx.exception))


# ---------------------------------------------------------------------------
# 7. reset clears task and context
# ---------------------------------------------------------------------------


class ResetTests(unittest.TestCase):
    def test_reset_clears_context_and_allows_fresh_first_step(self):
        adapter, _ = _build_adapter('do(action="Tap", element=[1,1])')
        adapter.act(_observation())
        self.assertEqual(adapter.planner.context_len(), 2)

        adapter.reset("a new task")
        self.assertEqual(adapter.planner.context_len(), 0)
        # A fresh first step works and sets the new task as the user prompt.
        adapter.act(_observation())
        ctx = adapter.planner.context_copy()
        self.assertIn("a new task", ctx[1]["content"][1]["text"]
                       if isinstance(ctx[1]["content"], list) else ctx[1]["content"])


# ---------------------------------------------------------------------------
# 8. Model error propagates
# ---------------------------------------------------------------------------


class ModelErrorTests(unittest.TestCase):
    def test_model_error_propagates_from_act(self):
        # Adapter that raises on the model call.
        fake = FakeModelClient(raises=RuntimeError("model down"))
        planner = ModelPlanner(fake, "SYS", "cn", verbose=False)
        parser = ActionParser(verbose=False, lang="cn")
        adapter = OpenAutoGLMAdapter(planner, parser)
        adapter.reset("打开设置")

        with self.assertRaises(RuntimeError):
            adapter.act(_observation())


if __name__ == "__main__":
    unittest.main()
