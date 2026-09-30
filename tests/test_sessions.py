import unittest
from uuid import UUID

from hermes_pbx_bridge.sessions import SessionRegistry


class SessionRegistryTests(unittest.TestCase):
    def test_reuses_one_conversation_for_each_call(self):
        registry = SessionRegistry()
        call_id = UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4")

        first = registry.open(call_id)
        first.previous_response_id = "resp_123"
        second = registry.open(call_id)

        self.assertIs(second, first)
        self.assertEqual(second.previous_response_id, "resp_123")

    def test_different_calls_do_not_share_conversation_state(self):
        registry = SessionRegistry()

        first = registry.open(UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4"))
        second = registry.open(UUID("ab769f87-4151-4acc-9439-a6968fdd343f"))

        self.assertIsNot(first, second)

    def test_close_discards_call_conversation(self):
        registry = SessionRegistry()
        call_id = UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4")
        first = registry.open(call_id)

        registry.close(call_id)

        self.assertIsNot(registry.open(call_id), first)


if __name__ == "__main__":
    unittest.main()
