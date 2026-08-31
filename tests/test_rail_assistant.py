import json
import unittest

from rtt_app.client import RTTResponse
from rtt_app.rail_assistant import RailAssistant


class FakeRTTClient:
    def info(self):
        return RTTResponse(
            data={"api_version": "test", "credentials": {"entitlements": []}},
            status=200,
            rate_limits={},
        )


class FakeOpenAIClient:
    def __init__(self):
        self.payloads = []

    def create(self, payload):
        self.payloads.append(payload)
        if len(self.payloads) == 1:
            return {
                "id": "resp_1",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "get_api_info",
                        "arguments": "{}",
                    }
                ],
            }
        tool_output = json.loads(payload["input"][0]["output"])
        assert tool_output["result"]["api_version"] == "test"
        return {
            "id": "resp_2",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "RTT API test."}],
                }
            ],
        }


class RailAssistantTests(unittest.TestCase):
    def test_executes_tool_and_returns_answer(self):
        api = FakeOpenAIClient()
        assistant = RailAssistant("unused", FakeRTTClient(), api_client=api)
        answer = assistant.ask("Which RTT version is active?")
        self.assertEqual(answer, "RTT API test.")
        self.assertEqual(api.payloads[1]["previous_response_id"], "resp_1")
        self.assertEqual(api.payloads[1]["input"][0]["call_id"], "call_1")

    def test_follow_up_uses_previous_response(self):
        api = FakeOpenAIClient()
        assistant = RailAssistant("unused", FakeRTTClient(), api_client=api)
        assistant.ask("Which RTT version is active?")
        api.payloads.clear()
        api.create = lambda payload: {
            "id": "resp_3",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Follow-up."}]}],
        }
        self.assertEqual(assistant.ask("And the entitlements?"), "Follow-up.")


if __name__ == "__main__":
    unittest.main()
