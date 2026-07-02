"""Model client tests against a local OpenAI-compatible HTTP service."""

import io
import json
import threading
import unittest
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from phone_agent.model.client import (
    ModelClient,
    ModelConfig,
    ModelServiceError,
    OpenAICompatibleHTTPClient,
)


class FakeAutoGLMHandler(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._send_json(
            {
                "object": "list",
                "data": [{"id": "autoglm-phone-9b", "object": "model"}],
            }
        )

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return

        payload = self._read_json()
        type(self).requests.append(payload)
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(
                b'data: {"choices":[{"delta":{"content":"think "}}]}\n\n'
            )
            self.wfile.write(
                b'data: {"choices":[{"delta":{"content":"do(action='
                b'\\"Tap\\", element=[1,1])"}}]}\n\n'
            )
            self.wfile.write(b"data: [DONE]\n\n")
            return

        self._send_json(
            {
                "choices": [
                    {"message": {"content": 'think finish(message="ok")'}}
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 2,
                    "total_tokens": 3,
                },
            }
        )

    def log_message(self, format, *args):
        return None

    def _read_json(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        return json.loads(body.decode("utf-8"))

    def _send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def fake_autoglm_service():
    FakeAutoGLMHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAutoGLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class ModelClientHTTPTests(unittest.TestCase):
    def test_streaming_request_uses_configured_local_service(self):
        with fake_autoglm_service() as base_url:
            client = ModelClient(
                ModelConfig(
                    base_url=base_url,
                    model_name="autoglm-phone-9b",
                    request_timeout=2.0,
                )
            )

            with redirect_stdout(io.StringIO()):
                response = client.request([{"role": "user", "content": "tap"}])

        self.assertEqual(response.thinking, "think")
        self.assertEqual(response.action, 'do(action="Tap", element=[1,1])')
        self.assertEqual(response.raw_content, 'think do(action="Tap", element=[1,1])')
        self.assertEqual(FakeAutoGLMHandler.requests[0]["model"], "autoglm-phone-9b")
        self.assertTrue(FakeAutoGLMHandler.requests[0]["stream"])

    def test_non_streaming_chat_completion_and_models_endpoint(self):
        with fake_autoglm_service() as base_url:
            client = OpenAICompatibleHTTPClient(base_url, timeout=2.0)
            response = client.create_chat_completion(
                {
                    "model": "autoglm-phone-9b",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                stream=False,
            )
            models = client.list_models()

        self.assertEqual(
            response["choices"][0]["message"]["content"],
            'think finish(message="ok")',
        )
        self.assertEqual(response["usage"]["total_tokens"], 3)
        self.assertEqual(models["data"][0]["id"], "autoglm-phone-9b")

    def test_service_errors_are_diagnostic(self):
        with fake_autoglm_service() as base_url:
            client = OpenAICompatibleHTTPClient(base_url, timeout=2.0)
            with self.assertRaises(ModelServiceError) as ctx:
                client._request_json("missing", method="GET")

        self.assertIn("/v1/missing", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
