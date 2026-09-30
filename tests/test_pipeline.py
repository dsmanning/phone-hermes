import io
import struct
import unittest
import wave
from uuid import UUID

from hermes_pbx_bridge.adapters import AgentReply
from hermes_pbx_bridge.pipeline import CallProcessor
from hermes_pbx_bridge.sessions import CallSession


class FakeStt:
    def transcribe(self, wav_audio):
        with wave.open(io.BytesIO(wav_audio), "rb") as wav:
            self.sample_rate = wav.getframerate()
        return "what time is it"


class FakeAgent:
    def __init__(self):
        self.calls = []

    def respond(self, text, previous_response_id):
        self.calls.append((text, previous_response_id))
        return AgentReply("It is noon.", "resp_next")


class FakeTts:
    def synthesize(self, text):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(struct.pack("<hh", 10, 20))
        return output.getvalue()


class CallProcessorTests(unittest.TestCase):
    def test_processes_turn_and_advances_call_conversation(self):
        stt = FakeStt()
        agent = FakeAgent()
        processor = CallProcessor(stt, agent, FakeTts())
        session = CallSession(UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4"), "resp_previous")

        reply_pcm = processor.process_turn(session, struct.pack("<hh", 100, 200))

        self.assertEqual(stt.sample_rate, 8000)
        self.assertEqual(agent.calls, [("what time is it", "resp_previous")])
        self.assertEqual(session.previous_response_id, "resp_next")
        self.assertEqual(reply_pcm, struct.pack("<hh", 10, 20))


if __name__ == "__main__":
    unittest.main()
