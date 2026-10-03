import io
import struct
import unittest
import wave
from uuid import UUID

from hermes_pbx_bridge.adapters import AdapterError, AgentReply
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


class FakeHome:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def respond(self, text):
        self.calls.append(text)
        return self.result


class FailingHome:
    def respond(self, text):
        raise AdapterError("Home Assistant request timed out")


class FakeTts:
    def __init__(self):
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(struct.pack("<hh", 10, 20))
        return output.getvalue()


class CallProcessorTests(unittest.TestCase):
    def test_falls_back_to_hermes_when_home_assistant_is_unavailable(self):
        agent = FakeAgent()
        processor = CallProcessor(FakeStt(), agent, FakeTts(), home=FailingHome())
        session = CallSession(UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4"))

        processor.process_turn(session, struct.pack("<hh", 100, 200))

        self.assertEqual(len(agent.calls), 1)
        self.assertEqual(session.previous_response_id, "resp_next")

    def test_uses_home_assistant_result_without_calling_hermes(self):
        home = FakeHome("Turned on the kitchen lights.")
        agent = FakeAgent()
        tts = FakeTts()
        processor = CallProcessor(FakeStt(), agent, tts, home=home)
        session = CallSession(UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4"))
        progress = []

        reply_pcm = processor.process_turn(
            session,
            struct.pack("<hh", 100, 200),
            progress.append,
        )

        self.assertEqual(home.calls, ["what time is it"])
        self.assertEqual(agent.calls, [])
        self.assertIsNone(session.previous_response_id)
        self.assertEqual(
            tts.calls,
            ["You said: what time is it. Please wait.", "Turned on the kitchen lights."],
        )
        self.assertEqual(reply_pcm, struct.pack("<hh", 10, 20))

    def test_falls_back_to_hermes_when_home_assistant_does_not_handle_command(self):
        home = FakeHome(None)
        agent = FakeAgent()
        processor = CallProcessor(FakeStt(), agent, FakeTts(), home=home)
        session = CallSession(UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4"))

        processor.process_turn(session, struct.pack("<hh", 100, 200))

        self.assertEqual(home.calls, ["what time is it"])
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual(session.previous_response_id, "resp_next")

    def test_processes_turn_and_advances_call_conversation(self):
        stt = FakeStt()
        agent = FakeAgent()
        tts = FakeTts()
        processor = CallProcessor(stt, agent, tts)
        session = CallSession(UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4"), "resp_previous")

        progress = []
        reply_pcm = processor.process_turn(
            session,
            struct.pack("<hh", 100, 200),
            progress.append,
        )

        self.assertEqual(stt.sample_rate, 8000)
        self.assertEqual(
            agent.calls,
            [(
                "Telephone command: what time is it\n"
                "Execute it now. Reply with one concise spoken sentence stating whether it "
                "succeeded. Never claim success unless a tool result confirms it.",
                "resp_previous",
            )],
        )
        self.assertEqual(session.previous_response_id, "resp_next")
        self.assertEqual(
            tts.calls,
            ["You said: what time is it. Please wait.", "It is noon."],
        )
        self.assertEqual(progress, [struct.pack("<hh", 10, 20)])
        self.assertEqual(reply_pcm, struct.pack("<hh", 10, 20))

    def test_synthesizes_opening_prompt(self):
        tts = FakeTts()
        processor = CallProcessor(FakeStt(), FakeAgent(), tts)
        session = CallSession(UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4"))

        opening_pcm = processor.begin_call(session)

        self.assertEqual(tts.calls, ["Chiron. Command."])
        self.assertEqual(opening_pcm[:16000], bytes(16000))
        self.assertEqual(opening_pcm[16000:], struct.pack("<hh", 10, 20))

    def test_generates_end_of_command_beep(self):
        processor = CallProcessor(FakeStt(), FakeAgent(), FakeTts())

        beep = processor.end_of_command()

        self.assertEqual(len(beep), 2400)
        self.assertNotEqual(beep, bytes(2400))

    def test_logs_sanitized_pipeline_stage_boundaries(self):
        processor = CallProcessor(FakeStt(), FakeAgent(), FakeTts())
        session = CallSession(UUID("2f2cb8ab-d13b-4f16-9ef8-424d8c118ab4"))

        with self.assertLogs("hermes_pbx_bridge.pipeline", level="INFO") as captured:
            processor.process_turn(session, struct.pack("<hh", 100, 200))

        messages = "\n".join(captured.output)
        self.assertIn("Speech-to-text completed", messages)
        self.assertIn("Hermes response completed", messages)
        self.assertIn("Text-to-speech completed", messages)
        self.assertNotIn("what time is it", messages)
        self.assertNotIn("It is noon", messages)


if __name__ == "__main__":
    unittest.main()
