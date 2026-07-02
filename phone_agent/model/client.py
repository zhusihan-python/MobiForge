"""Model client for OpenAI-compatible local or remote inference services."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from phone_agent.config.i18n import get_message


class ModelServiceError(RuntimeError):
    """Raised when an OpenAI-compatible model service request fails."""


@dataclass
class ModelConfig:
    """Configuration for the AI model service."""

    base_url: str = "http://localhost:8000/v1"
    api_key: str = "EMPTY"
    model_name: str = "autoglm-phone-9b"
    max_tokens: int = 3000
    temperature: float = 0.0
    top_p: float = 0.85
    frequency_penalty: float = 0.2
    extra_body: dict[str, Any] = field(default_factory=dict)
    request_timeout: float = 30.0
    lang: str = "cn"  # Language for UI messages: 'cn' or 'en'


@dataclass
class ModelResponse:
    """Response from the AI model."""

    thinking: str
    action: str
    raw_content: str
    # Performance metrics
    time_to_first_token: float | None = None  # Time to first token (seconds)
    time_to_thinking_end: float | None = None  # Time to thinking end (seconds)
    total_time: float | None = None  # Total inference time (seconds)


class ModelClient:
    """Client for OpenAI-compatible vision-language model services."""

    def __init__(self, config: ModelConfig | None = None):
        self.config = config or ModelConfig()
        self.client = OpenAICompatibleHTTPClient(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.request_timeout,
        )

    def request(self, messages: list[dict[str, Any]]) -> ModelResponse:
        """Send a streaming request to the model and parse its action output."""
        start_time = time.time()
        time_to_first_token = None
        time_to_thinking_end = None

        stream = self.client.create_chat_completion(
            {
                "messages": messages,
                "model": self.config.model_name,
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "top_p": self.config.top_p,
                "frequency_penalty": self.config.frequency_penalty,
                **self.config.extra_body,
            },
            stream=True,
        )

        raw_content = ""
        buffer = ""
        action_markers = ["finish(message=", "do(action="]
        in_action_phase = False
        first_token_received = False

        for content in stream:
            raw_content += content

            if not first_token_received:
                time_to_first_token = time.time() - start_time
                first_token_received = True

            if in_action_phase:
                continue

            buffer += content

            marker_found = False
            for marker in action_markers:
                if marker in buffer:
                    thinking_part = buffer.split(marker, 1)[0]
                    _console_print(thinking_part, end="", flush=True)
                    _console_print()
                    in_action_phase = True
                    marker_found = True
                    if time_to_thinking_end is None:
                        time_to_thinking_end = time.time() - start_time
                    break

            if marker_found:
                continue

            is_potential_marker = False
            for marker in action_markers:
                for i in range(1, len(marker)):
                    if buffer.endswith(marker[:i]):
                        is_potential_marker = True
                        break
                if is_potential_marker:
                    break

            if not is_potential_marker:
                _console_print(buffer, end="", flush=True)
                buffer = ""

        total_time = time.time() - start_time
        thinking, action = self._parse_response(raw_content)
        self._print_metrics(time_to_first_token, time_to_thinking_end, total_time)
        return ModelResponse(
            thinking=thinking,
            action=action,
            raw_content=raw_content,
            time_to_first_token=time_to_first_token,
            time_to_thinking_end=time_to_thinking_end,
            total_time=total_time,
        )

    def _print_metrics(
        self,
        time_to_first_token: float | None,
        time_to_thinking_end: float | None,
        total_time: float,
    ) -> None:
        lang = self.config.lang
        _console_print()
        _console_print("=" * 50)
        _console_print(f"{get_message('performance_metrics', lang)}:")
        _console_print("-" * 50)
        if time_to_first_token is not None:
            _console_print(
                f"{get_message('time_to_first_token', lang)}: {time_to_first_token:.3f}s"
            )
        if time_to_thinking_end is not None:
            _console_print(
                f"{get_message('time_to_thinking_end', lang)}:        {time_to_thinking_end:.3f}s"
            )
        _console_print(
            f"{get_message('total_inference_time', lang)}:          {total_time:.3f}s"
        )
        _console_print("=" * 50)

    def _parse_response(self, content: str) -> tuple[str, str]:
        """
        Parse the model response into thinking and action parts.

        Parsing rules:
        1. If content contains 'finish(message=', everything before is thinking,
           everything from 'finish(message=' onwards is action.
        2. If rule 1 doesn't apply but content contains 'do(action=',
           everything before is thinking, everything from 'do(action=' onwards is action.
        3. Fallback: If content contains '<answer>', use legacy parsing with XML tags.
        4. Otherwise, return empty thinking and full content as action.
        """
        if "finish(message=" in content:
            parts = content.split("finish(message=", 1)
            thinking = parts[0].strip()
            action = "finish(message=" + parts[1]
            return thinking, action

        if "do(action=" in content:
            parts = content.split("do(action=", 1)
            thinking = parts[0].strip()
            action = "do(action=" + parts[1]
            return thinking, action

        if "<answer>" in content:
            parts = content.split("<answer>", 1)
            thinking = parts[0].replace("<think>", "").replace("</think>", "").strip()
            action = parts[1].replace("</answer>", "").strip()
            return thinking, action

        return "", content


class OpenAICompatibleHTTPClient:
    """Tiny OpenAI-compatible HTTP client using only the standard library."""

    def __init__(self, base_url: str, api_key: str = "EMPTY", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def create_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        stream: bool,
    ):
        request_payload = dict(payload)
        request_payload["stream"] = stream
        if stream:
            return self._stream_chat_completion(request_payload)
        return self._request_json("chat/completions", request_payload, method="POST")

    def list_models(self) -> dict[str, Any]:
        return self._request_json("models", method="GET")

    def _stream_chat_completion(self, payload: dict[str, Any]):
        with self._open(
            "chat/completions",
            method="POST",
            payload=payload,
            accept="text/event-stream",
        ) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line or line.startswith(":"):
                    continue
                data = line[5:].strip() if line.startswith("data:") else line
                if data == "[DONE]":
                    break
                event = _loads_json(data, "stream event")
                content = _extract_stream_content(event)
                if content:
                    yield content

    def _request_json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        method: str,
    ) -> dict[str, Any]:
        with self._open(path, method=method, payload=payload) as response:
            text = response.read().decode("utf-8")
        return _loads_json(text, "response body")

    def _open(
        self,
        path: str,
        *,
        method: str,
        payload: dict[str, Any] | None = None,
        accept: str = "application/json",
    ):
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {
            "Accept": accept,
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(
            _join_url(self.base_url, path),
            data=body,
            headers=headers,
            method=method,
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            finally:
                exc.close()
            raise ModelServiceError(
                f"Model service HTTP {exc.code} at {request.full_url}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelServiceError(
                f"Model service request failed at {request.full_url}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise ModelServiceError(
                f"Model service request timed out at {request.full_url}"
            ) from exc


class MessageBuilder:
    """Helper class for building conversation messages."""

    @staticmethod
    def create_system_message(content: str) -> dict[str, Any]:
        """Create a system message."""
        return {"role": "system", "content": content}

    @staticmethod
    def create_user_message(
        text: str, image_base64: str | None = None
    ) -> dict[str, Any]:
        """
        Create a user message with optional image.

        Args:
            text: Text content.
            image_base64: Optional base64-encoded image.

        Returns:
            Message dictionary.
        """
        content = []

        if image_base64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                }
            )

        content.append({"type": "text", "text": text})

        return {"role": "user", "content": content}

    @staticmethod
    def create_assistant_message(content: str) -> dict[str, Any]:
        """Create an assistant message."""
        return {"role": "assistant", "content": content}

    @staticmethod
    def remove_images_from_message(message: dict[str, Any]) -> dict[str, Any]:
        """
        Remove image content from a message to save context space.

        Args:
            message: Message dictionary.

        Returns:
            Message with images removed.
        """
        if isinstance(message.get("content"), list):
            message["content"] = [
                item for item in message["content"] if item.get("type") == "text"
            ]
        return message

    @staticmethod
    def build_screen_info(current_app: str, **extra_info) -> str:
        """
        Build screen info string for the model.

        Args:
            current_app: Current app name.
            **extra_info: Additional info to include.

        Returns:
            JSON string with screen info.
        """
        info = {"current_app": current_app, **extra_info}
        return json.dumps(info, ensure_ascii=False)


def _extract_stream_content(event: dict[str, Any]) -> str | None:
    choices = event.get("choices") or []
    if not choices:
        return None
    choice = choices[0]
    delta = choice.get("delta")
    if isinstance(delta, dict) and delta.get("content") is not None:
        return str(delta["content"])
    message = choice.get("message")
    if isinstance(message, dict) and message.get("content") is not None:
        return str(message["content"])
    text = choice.get("text")
    return str(text) if text is not None else None


def _loads_json(text: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelServiceError(f"Invalid JSON in model service {label}: {text}") from exc
    if not isinstance(payload, dict):
        raise ModelServiceError(f"Unexpected model service {label}: {payload!r}")
    return payload


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _console_print(*args, **kwargs) -> None:
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        file = kwargs.get("file") or sys.stdout
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        text = sep.join(str(arg) for arg in args) + end
        encoding = getattr(file, "encoding", None) or "utf-8"
        if hasattr(file, "buffer"):
            file.buffer.write(text.encode(encoding, errors="replace"))
            if kwargs.get("flush", False):
                file.flush()
        else:
            file.write(
                text.encode(encoding, errors="replace").decode(
                    encoding, errors="replace"
                )
            )
