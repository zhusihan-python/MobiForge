"""Main PhoneAgent class for orchestrating phone automation."""

import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from phone_agent.actions import ActionHandler
from phone_agent.actions.handler import finish
from phone_agent.config import get_messages, get_system_prompt
from phone_agent.device_factory import get_device_factory
from phone_agent.model import ModelClient, ModelConfig
from phone_agent.planner import ActionParser, ModelPlanner


@dataclass
class AgentConfig:
    """Configuration for the PhoneAgent."""

    max_steps: int = 100
    device_id: str | None = None
    lang: str = "cn"
    system_prompt: str | None = None
    verbose: bool = True

    def __post_init__(self):
        if self.system_prompt is None:
            self.system_prompt = get_system_prompt(self.lang)


@dataclass
class StepResult:
    """Result of a single agent step."""

    success: bool
    finished: bool
    action: dict[str, Any] | None
    thinking: str
    message: str | None = None
    screenshot_base64: str | None = None
    screenshot_width: int | None = None
    screenshot_height: int | None = None
    screenshot_is_sensitive: bool = False
    current_app: str | None = None
    model_output: str | None = None
    duration_ms: int | None = None


class PhoneAgent:
    """
    AI-powered agent for automating Android phone interactions.

    The agent uses a vision-language model to understand screen content
    and decide on actions to complete user tasks.

    Args:
        model_config: Configuration for the AI model.
        agent_config: Configuration for the agent behavior.
        confirmation_callback: Optional callback for sensitive action confirmation.
        takeover_callback: Optional callback for takeover requests.

    Example:
        >>> from phone_agent import PhoneAgent
        >>> from phone_agent.model import ModelConfig
        >>>
        >>> model_config = ModelConfig(base_url="http://localhost:8000/v1")
        >>> agent = PhoneAgent(model_config)
        >>> agent.run("Open WeChat and send a message to John")
    """

    def __init__(
        self,
        model_config: ModelConfig | None = None,
        agent_config: AgentConfig | None = None,
        confirmation_callback: Callable[[str], bool] | None = None,
        takeover_callback: Callable[[str], None] | None = None,
    ):
        self.model_config = model_config or ModelConfig()
        self.agent_config = agent_config or AgentConfig()

        self.model_client = ModelClient(self.model_config)
        self.action_handler = ActionHandler(
            device_id=self.agent_config.device_id,
            confirmation_callback=confirmation_callback,
            takeover_callback=takeover_callback,
        )

        # Prompt/model concern lives on the planner; parse on the parser. Both
        # are faithful extractions from the old _execute_step and will become the
        # trunk for OpenAutoGLMAdapter (Gate B, next step).
        self.planner = ModelPlanner(
            model_client=self.model_client,
            system_prompt=self.agent_config.system_prompt,
            lang=self.agent_config.lang,
            verbose=self.agent_config.verbose,
        )
        self.parser = ActionParser(
            verbose=self.agent_config.verbose, lang=self.agent_config.lang
        )

        self._step_count = 0

    def run(self, task: str) -> str:
        """
        Run the agent to complete a task.

        Args:
            task: Natural language description of the task.

        Returns:
            Final message from the agent.
        """
        self.planner.reset()
        self._step_count = 0

        # First step with user prompt
        result = self._execute_step(task, is_first=True)

        if result.finished:
            return result.message or "Task completed"

        # Continue until finished or max steps reached
        while self._step_count < self.agent_config.max_steps:
            result = self._execute_step(is_first=False)

            if result.finished:
                return result.message or "Task completed"

        return "Max steps reached"

    def step(self, task: str | None = None) -> StepResult:
        """
        Execute a single step of the agent.

        Useful for manual control or debugging.

        Args:
            task: Task description (only needed for first step).

        Returns:
            StepResult with step details.
        """
        is_first = self.planner.context_len() == 0

        if is_first and not task:
            raise ValueError("Task is required for the first step")

        return self._execute_step(task, is_first)

    def reset(self) -> None:
        """Reset the agent state for a new task."""
        self.planner.reset()
        self._step_count = 0

    def _execute_step(
        self, user_prompt: str | None = None, is_first: bool = False
    ) -> StepResult:
        """Execute a single step of the agent loop.

        Step order is locked (Gate B extraction contract):
        observe -> planner.plan -> parser.parse -> planner.strip_last_image
        -> action_handler.execute -> planner.append_assistant -> finished.
        """
        step_started_at = time.perf_counter()
        self._step_count += 1

        # Capture current screen state (observation — stays on the agent for now;
        # moves to EnvBackend in a later Gate-B step).
        device_factory = get_device_factory()
        screenshot = device_factory.get_screenshot(self.agent_config.device_id)
        current_app = device_factory.get_current_app(self.agent_config.device_id)

        # Build messages + call model. plan() lets model errors propagate; the
        # existing try/except below preserves the original failure StepResult.
        try:
            response = self.planner.plan(
                screenshot_base64=screenshot.base64_data,
                current_app=current_app,
                user_prompt=user_prompt,
                is_first=is_first,
            )
        except Exception as e:
            if self.agent_config.verbose:
                traceback.print_exc()
            return StepResult(
                success=False,
                finished=True,
                action=None,
                thinking="",
                message=f"Model error: {e}",
                screenshot_base64=screenshot.base64_data,
                screenshot_width=screenshot.width,
                screenshot_height=screenshot.height,
                screenshot_is_sensitive=screenshot.is_sensitive,
                current_app=current_app,
                duration_ms=int((time.perf_counter() - step_started_at) * 1000),
            )

        # Parse action from response (parse owns the verbose action dump).
        action = self.parser.parse(response.action)

        # Remove image from context to save space.
        self.planner.strip_last_image()

        # Execute action (execution stays on the agent for now).
        try:
            result = self.action_handler.execute(
                action, screenshot.width, screenshot.height
            )
        except Exception as e:
            if self.agent_config.verbose:
                traceback.print_exc()
            result = self.action_handler.execute(
                finish(message=str(e)), screenshot.width, screenshot.height
            )

        # Add assistant response to context.
        self.planner.append_assistant(response.thinking, response.action)

        # Check if finished
        finished = action.get("_metadata") == "finish" or result.should_finish

        if finished and self.agent_config.verbose:
            msgs = get_messages(self.agent_config.lang)
            print("\n" + "🎉 " + "=" * 48)
            print(
                f"✅ {msgs['task_completed']}: {result.message or action.get('message', msgs['done'])}"
            )
            print("=" * 50 + "\n")

        return StepResult(
            success=result.success,
            finished=finished,
            action=action,
            thinking=response.thinking,
            message=result.message or action.get("message"),
            screenshot_base64=screenshot.base64_data,
            screenshot_width=screenshot.width,
            screenshot_height=screenshot.height,
            screenshot_is_sensitive=screenshot.is_sensitive,
            current_app=current_app,
            model_output=response.raw_content,
            duration_ms=int((time.perf_counter() - step_started_at) * 1000),
        )

    @property
    def context(self) -> list[dict[str, Any]]:
        """Get the current conversation context (shallow copy)."""
        return self.planner.context_copy()

    @property
    def step_count(self) -> int:
        """Get the current step count."""
        return self._step_count
