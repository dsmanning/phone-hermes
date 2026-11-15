import io
from pathlib import Path
import tempfile
import unittest
import wave

from hermes_pbx_bridge.speech_cli import parse_proxy_address, synthesize_to_path


def wav_bytes(pcm=b"\x01\x00\x02\x00"):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setframerate(16000)
        wav_file.setsampwidth(2)
        wav_file.setnchannels(1)
        wav_file.writeframes(pcm)
    return output.getvalue()


class SpeechCliTests(unittest.TestCase):
    def test_parses_uncredentialed_http_proxy(self):
        self.assertEqual(
            parse_proxy_address("http://egress-proxy:3128"),
            ("egress-proxy", 3128),
        )

    def test_transcribe_reads_wav_and_returns_transcript(self):
        class FakeAdapter:
            def transcribe(self, audio):
                self.audio = audio
                return "Recognized speech"

        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.wav"
            source.write_bytes(wav_bytes())

            from hermes_pbx_bridge.speech_cli import transcribe_path

            transcript = transcribe_path(source, adapter)

            self.assertEqual(transcript, "Recognized speech")
            self.assertEqual(adapter.audio, wav_bytes())

    def test_synthesize_writes_valid_wav_atomically(self):
        class FakeAdapter:
            def synthesize(self, text):
                self.text = text
                return wav_bytes()

        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "speech.wav"

            synthesize_to_path("Hello from Calliope", output, adapter)

            self.assertEqual(adapter.text, "Hello from Calliope")
            self.assertEqual(output.read_bytes(), wav_bytes())
            self.assertEqual(list(Path(directory).glob("*.partial")), [])
            with wave.open(str(output), "rb") as wav_file:
                self.assertEqual(wav_file.getframerate(), 16000)
                self.assertEqual(wav_file.getsampwidth(), 2)
                self.assertEqual(wav_file.getnchannels(), 1)


if __name__ == "__main__":
    unittest.main()
