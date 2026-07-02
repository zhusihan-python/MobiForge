"""Model client optional-dependency behavior."""

import unittest
from unittest.mock import patch

from phone_agent.model.client import ModelClient, ModelConfig


class ModelClientDependencyTests(unittest.TestCase):
    def test_missing_openai_sdk_is_reported_on_request_not_import(self):
        import_error = ModuleNotFoundError(
            "No module named 'openai'",
            name="openai",
        )

        with patch(
            "phone_agent.model.client._load_openai_client_class",
            return_value=(None, import_error),
        ):
            client = ModelClient(ModelConfig())

        self.assertIsNone(client.client)
        with self.assertRaises(RuntimeError) as ctx:
            client.request([])

        self.assertIn("OpenAI SDK is not installed", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, import_error)

    def test_openai_client_is_created_when_sdk_is_available(self):
        class FakeOpenAI:
            def __init__(self, base_url, api_key):
                self.base_url = base_url
                self.api_key = api_key

        with patch(
            "phone_agent.model.client._load_openai_client_class",
            return_value=(FakeOpenAI, None),
        ):
            client = ModelClient(
                ModelConfig(base_url="http://example.test/v1", api_key="key")
            )

        self.assertIsInstance(client.client, FakeOpenAI)
        self.assertEqual(client.client.base_url, "http://example.test/v1")
        self.assertEqual(client.client.api_key, "key")


if __name__ == "__main__":
    unittest.main()
