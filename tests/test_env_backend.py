"""Gate B Step 2b: AdbDeviceEnv wraps device_factory + ActionHandler behind
EnvBackend. Tested with fake factory/handler — no real device.

Locks: observe->execute ordering, handler-exception handling, WAIT duration
reversal, per-type reverse mapping, coordinate pass-through (no clamp),
ActionResult->EnvResult wrapping (transparent message), defensive raises for
non-executable types, screenshot-size flow, and that `raw` is never used as an
execution shortcut.
"""

import unittest

from phone_agent.actions.handler import ActionResult
from runtime.env_backends import AdbDeviceEnv
from runtime.schemas import Action, ActionType


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeScreenshot:
    def __init__(self, base64_data="IMG", width=1080, height=2400, is_sensitive=False):
        self.base64_data = base64_data
        self.width = width
        self.height = height
        self.is_sensitive = is_sensitive


class FakeFactory:
    def __init__(self, screenshot=None, current_app="Settings"):
        self._screenshot = screenshot or FakeScreenshot()
        self._current_app = current_app

    def get_screenshot(self, device_id=None, timeout=10):
        return self._screenshot

    def get_current_app(self, device_id=None):
        return self._current_app


class FakeActionHandler:
    """Records the (dict, width, height) it was called with; returns a scripted
    ActionResult or raises."""

    def __init__(self, result=None, raises=None):
        self._result = result if result is not None else ActionResult(success=True, should_finish=False)
        self._raises = raises
        self.calls = []  # list of (dict, width, height)

    def execute(self, action_dict, width, height):
        self.calls.append((action_dict, width, height))
        if self._raises is not None:
            raise self._raises
        return self._result


class ProtocolCheckingHandler(FakeActionHandler):
    """Enforces the real ActionHandler.execute() protocol (handler.py:59): the
    dict must carry ``_metadata`` == "do" (or "finish") or the handler returns
    ``Unknown action type`` *without* dispatching. Used to prove the reverse
    mapping produces dicts a real handler would actually execute (review P1)."""

    def execute(self, action_dict, width, height):
        self.calls.append((action_dict, width, height))
        metadata = action_dict.get("_metadata")
        if metadata not in ("do", "finish"):
            return ActionResult(
                success=False,
                should_finish=True,
                message=f"Unknown action type: {metadata}",
            )
        if self._raises is not None:
            raise self._raises
        return self._result


def _env(handler=None, factory=None):
    return AdbDeviceEnv(
        device_factory=factory or FakeFactory(),
        action_handler=handler or FakeActionHandler(),
    )


def _act(type_, **kw):
    return Action(type=type_, **kw)


# ---------------------------------------------------------------------------
# 1. execute() before observe() raises
# ---------------------------------------------------------------------------


class OrderingTests(unittest.TestCase):
    def test_execute_before_observe_raises(self):
        env = _env()
        with self.assertRaises(RuntimeError) as ctx:
            env.execute(_act(ActionType.TAP, coordinates=[1, 1]))
        self.assertIn("observe() must be called before execute()", str(ctx.exception))


# ---------------------------------------------------------------------------
# 2. handler exception -> EnvResult(success=False)
# ---------------------------------------------------------------------------


class HandlerExceptionTests(unittest.TestCase):
    def test_handler_exception_becomes_failed_env_result(self):
        handler = FakeActionHandler(raises=RuntimeError("device disconnected"))
        env = _env(handler=handler)
        env.observe()
        result = env.execute(_act(ActionType.BACK))

        self.assertFalse(result.success)
        self.assertIn("Action failed: device disconnected", result.message)


# ---------------------------------------------------------------------------
# 3. WAIT duration reversal
# ---------------------------------------------------------------------------


class WaitDurationTests(unittest.TestCase):
    def test_none_duration_uses_legacy_default(self):
        handler = FakeActionHandler()
        env = _env(handler=handler)
        env.observe()
        env.execute(_act(ActionType.WAIT))
        self.assertEqual(handler.calls[0][0]["duration"], "1 seconds")

    def test_explicit_ms_round_trips(self):
        handler = FakeActionHandler()
        env = _env(handler=handler)
        env.observe()
        env.execute(_act(ActionType.WAIT, duration_ms=3000))
        # Not decimal-bound: handler parses float(s.replace("seconds","")).
        self.assertEqual(handler.calls[0][0]["duration"], "3 seconds")


# ---------------------------------------------------------------------------
# 4. observe maps Screenshot + app -> Observation
# ---------------------------------------------------------------------------


class ObserveMappingTests(unittest.TestCase):
    def test_observe_builds_observation_from_factory(self):
        shot = FakeScreenshot(base64_data="B64", width=720, height=1600, is_sensitive=True)
        factory = FakeFactory(screenshot=shot, current_app="WeChat")
        env = _env(factory=factory)

        obs = env.observe()

        self.assertEqual(obs.screenshot, "B64")
        self.assertEqual(obs.screenshot_size.width, 720)
        self.assertEqual(obs.screenshot_size.height, 1600)
        self.assertEqual(obs.current_app, "WeChat")
        self.assertTrue(obs.is_sensitive)
        self.assertEqual(obs.device.backend, "adb")
        # ADB cannot read structured app state — must stay None.
        self.assertIsNone(obs.env_state)


# ---------------------------------------------------------------------------
# 5. per-type reverse mapping
# ---------------------------------------------------------------------------


class ReverseMappingTests(unittest.TestCase):
    CASES = [
        (ActionType.TAP, {"coordinates": [500, 500]}, "Tap", {"element": [500, 500]}),
        (ActionType.DOUBLE_TAP, {"coordinates": [10, 20]}, "Double Tap", {"element": [10, 20]}),
        (ActionType.LONG_PRESS, {"coordinates": [990, 5]}, "Long Press", {"element": [990, 5]}),
        (ActionType.TYPE_TEXT, {"text": "hi"}, "Type", {"text": "hi"}),
        (ActionType.SWIPE, {"coordinates": [100, 800, 100, 100]}, "Swipe",
         {"start": [100, 800], "end": [100, 100]}),
        (ActionType.BACK, {}, "Back", {}),
        (ActionType.HOME, {}, "Home", {}),
        (ActionType.LAUNCH_APP, {"app": "WeChat"}, "Launch", {"app": "WeChat"}),
        (ActionType.WAIT, {"duration_ms": 2000}, "Wait", {"duration": "2 seconds"}),
    ]

    def test_each_executable_type_maps_to_expected_legacy_dict(self):
        for atype, action_kw, expected_name, expected_extra in self.CASES:
            with self.subTest(action=atype):
                # ProtocolCheckingHandler rejects dicts missing _metadata=="do",
                # so this also proves a real ActionHandler would dispatch them.
                handler = ProtocolCheckingHandler()
                env = _env(handler=handler)
                env.observe()
                result = env.execute(_act(atype, **action_kw))

                got_dict = handler.calls[0][0]
                self.assertEqual(got_dict["action"], expected_name)
                self.assertEqual(got_dict["_metadata"], "do")
                for k, v in expected_extra.items():
                    self.assertEqual(got_dict[k], v, msg=f"{k} mismatch for {atype}")
                # The handler accepted the protocol (not "Unknown action type").
                self.assertTrue(result.success, msg=f"handler rejected {atype}: {result.message}")


# ---------------------------------------------------------------------------
# 6. coordinates pass through as ints, no clamp
# ---------------------------------------------------------------------------


class CoordinatePassthroughTests(unittest.TestCase):
    def test_float_coordinates_cast_to_int_without_clamping(self):
        handler = FakeActionHandler()
        env = _env(handler=handler)
        env.observe()
        env.execute(_act(ActionType.TAP, coordinates=[500.7, 999.9]))

        # int() truncates toward zero; no clamping to any range.
        self.assertEqual(handler.calls[0][0]["element"], [500, 999])


# ---------------------------------------------------------------------------
# 7. ActionResult -> EnvResult wrapping (transparent message)
# ---------------------------------------------------------------------------


class ResultWrappingTests(unittest.TestCase):
    def test_success_result_message_passes_through_as_none(self):
        handler = FakeActionHandler(result=ActionResult(success=True, should_finish=False))
        env = _env(handler=handler)
        env.observe()
        result = env.execute(_act(ActionType.BACK))

        self.assertTrue(result.success)
        self.assertFalse(result.should_finish)
        # No "ok" stuffed in — transparent passthrough for oracle cleanliness.
        self.assertIsNone(result.message)

    def test_should_finish_propagates(self):
        handler = FakeActionHandler(
            result=ActionResult(success=False, should_finish=True, message="cancelled")
        )
        env = _env(handler=handler)
        env.observe()
        result = env.execute(_act(ActionType.BACK))

        self.assertFalse(result.success)
        self.assertTrue(result.should_finish)
        self.assertEqual(result.message, "cancelled")


# ---------------------------------------------------------------------------
# 8. defensive: non-executable types raise
# ---------------------------------------------------------------------------


class DefensiveRaiseTests(unittest.TestCase):
    def test_non_executable_types_raise_in_execute(self):
        env = _env()
        env.observe()
        for atype in (ActionType.NOOP, ActionType.FINISH, ActionType.REQUEST_USER):
            with self.subTest(action=atype):
                with self.assertRaises(ValueError):
                    env.execute(_act(atype, text="x"))


# ---------------------------------------------------------------------------
# 9. screenshot_size from observe flows into execute
# ---------------------------------------------------------------------------


class SizeFlowTests(unittest.TestCase):
    def test_observed_size_passed_to_handler_in_execute(self):
        shot = FakeScreenshot(width=1440, height=3120)
        handler = FakeActionHandler()
        env = _env(factory=FakeFactory(screenshot=shot), handler=handler)

        env.observe()
        env.execute(_act(ActionType.TAP, coordinates=[1, 1]))

        _, width, height = handler.calls[0]
        self.assertEqual((width, height), (1440, 3120))


# ---------------------------------------------------------------------------
# 10. raw is NOT used as an execution shortcut
# ---------------------------------------------------------------------------


class RawNotPreferredTests(unittest.TestCase):
    def test_execute_rebuilds_from_normalized_fields_ignoring_raw(self):
        # raw carries a bogus legacy dict that would mislead if preferred.
        bogus_raw = {"action": "Back", "_metadata": "do"}
        handler = FakeActionHandler()
        env = _env(handler=handler)
        env.observe()
        env.execute(_act(ActionType.TAP, coordinates=[300, 400], raw=bogus_raw))

        # The handler must have received a Tap dict built from coordinates, not
        # the bogus Back dict from raw.
        self.assertEqual(handler.calls[0][0]["action"], "Tap")
        self.assertEqual(handler.calls[0][0]["element"], [300, 400])


if __name__ == "__main__":
    unittest.main()
