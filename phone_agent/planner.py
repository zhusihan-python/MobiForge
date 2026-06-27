"""Legacy-compatible planner (Gate B, Step 1 — transitional scaffolding).

This module is **faithful-extraction scaffolding**, not the desired final clean
adapter boundary. It pulls two responsibilities out of
``PhoneAgent._execute_step()`` so that ``OpenAutoGLMAdapter`` (next Gate-B step)
can be built on a stable trunk:

- ``ModelPlanner`` — message construction, the conversation ``_context`` list,
  the "thinking" header print, and the model call (``model_client.request``).
- ``ActionParser`` — model text -> action dict, the ``ValueError -> finish``
  fallback, and the verbose parsed-action JSON print.

Everything here reproduces the inline behavior of ``PhoneAgent._execute_step()``
verbatim. In particular: model errors **propagate** from ``ModelPlanner.plan()``
so that ``PhoneAgent`` keeps its existing ``try/except`` (no sentinel type), and
the print side-effects are preserved. When ``OpenAutoGLMAdapter`` lands, this
module is expected to shrink/reshape; do not treat it as the target architecture.

See ``docs/adr/0001-mobilegym-positioning.md`` (Migration plan) and the Gate B
plan for the locked step order.
"""

from __future__ import annotations

import json
import traceback
from typing import Any, Optional

from phone_agent.actions.handler import finish, parse_action
from phone_agent.config import get_messages
from phone_agent.model.client import MessageBuilder, ModelClient, ModelResponse


class ModelPlanner:
    """Owns prompt construction + model call, lifted from ``PhoneAgent``.

    Holds the conversation ``_context`` so ``PhoneAgent`` can keep its
    ``context`` / ``reset`` / first-step-detection behavior by delegating here.
    """

    def __init__(self, model_client: ModelClient, system_prompt: str, lang: str, verbose: bool):
        self.model_client = model_client
        self.system_prompt = system_prompt
        self.lang = lang
        self.verbose = verbose
        self._context: list[dict[str, Any]] = []

    # -- context accessors (PhoneAgent delegates its public surface here) --

    def reset(self) -> None:
        self._context = []

    def context_len(self) -> int:
        return len(self._context)

    def context_copy(self) -> list[dict[str, Any]]:
        """Return a shallow copy of the context (mirrors PhoneAgent.context)."""
        return self._context.copy()

    def strip_last_image(self) -> None:
        """Remove the image from the last context message to save space."""
        self._context[-1] = MessageBuilder.remove_images_from_message(self._context[-1])

    def append_assistant(self, thinking: str, action: str) -> None:
        """Append the assistant turn reconstructed from the model response."""
        self._context.append(
            MessageBuilder.create_assistant_message(
                f"<think>{thinking}</think><answer>{action}</answer>"
            )
        )

    # -- the model call --

    def plan(
        self,
        *,
        screenshot_base64: str,
        current_app: str,
        user_prompt: Optional[str] = None,
        is_first: bool = False,
    ) -> ModelResponse:
        """Build messages, print the thinking header, and call the model.

        Raises whatever ``model_client.request`` raises; the caller
        (``PhoneAgent._execute_step``) keeps the existing error handling.
        """
        if is_first:
            self._context.append(
                MessageBuilder.create_system_message(self.system_prompt)
            )

            screen_info = MessageBuilder.build_screen_info(current_app)
            text_content = f"{user_prompt}\n\n{screen_info}"

            self._context.append(
                MessageBuilder.create_user_message(
                    text=text_content, image_base64=screenshot_base64
                )
            )
        else:
            screen_info = MessageBuilder.build_screen_info(current_app)
            text_content = f"** Screen Info **\n\n{screen_info}"

            self._context.append(
                MessageBuilder.create_user_message(
                    text=text_content, image_base64=screenshot_base64
                )
            )

        msgs = get_messages(self.lang)
        print("\n" + "=" * 50)
        print(f"💭 {msgs['thinking']}:")
        print("-" * 50)
        return self.model_client.request(self._context)


class ActionParser:
    """Owns model text -> action dict + the verbose action dump."""

    def __init__(self, verbose: bool, lang: str = "cn"):
        self.verbose = verbose
        self.lang = lang

    def parse(self, response_action: str) -> dict[str, Any]:
        """Parse the model's action text, falling back to ``finish`` on error.

        Mirrors ``PhoneAgent._execute_step``: on ``ValueError`` from
        ``parse_action``, the raw action text becomes a ``finish`` message. The
        verbose dump reuses the same ``get_messages(lang)`` lookup as the inline
        code (``msgs`` is shared across the plan/parse prints in the original).
        """
        try:
            action = parse_action(response_action)
        except ValueError:
            # Preserve the original verbose behavior: print the traceback before
            # falling back to finish (was inline in PhoneAgent._execute_step).
            if self.verbose:
                traceback.print_exc()
            action = finish(message=response_action)

        if self.verbose:
            msgs = get_messages(self.lang)
            print("-" * 50)
            print(f"🎯 {msgs['action']}:")
            print(json.dumps(action, ensure_ascii=False, indent=2))
            print("=" * 50 + "\n")

        return action
