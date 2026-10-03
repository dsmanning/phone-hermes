import io
import struct
import subprocess
import sys
import unittest
import wave

from hermes_pbx_bridge.audio import pcm16le_to_wav, tone_pcm16le, wav_to_pcm16le_8khz


class AudioConversionTests(unittest.TestCase):
    def test_generates_bounded_command_end_tone(self):
        tone = tone_pcm16le(frequency_hz=1000, duration_ms=150, sample_rate=8000)

        self.assertEqual(len(tone), 2400)
        samples = struct.unpack("<" + "h" * 1200, tone)
        self.assertTrue(any(samples))
        self.assertLessEqual(max(abs(sample) for sample in samples), 8000)

    def test_imports_when_removed_audioop_module_is_unavailable(self):
        program = """
import importlib.abc
import sys
class BlockAudioop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'audioop':
            raise ModuleNotFoundError("blocked to emulate Python 3.13")
        return None
sys.meta_path.insert(0, BlockAudioop())
import hermes_pbx_bridge.audio
"""

        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=".",
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_wraps_8khz_mono_pcm_in_wave_container(self):
        pcm = struct.pack("<hhh", -1000, 0, 1000)

        wav_bytes = pcm16le_to_wav(pcm)

        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getframerate(), 8000)
            self.assertEqual(wav.readframes(wav.getnframes()), pcm)

    def test_resamples_16khz_wave_to_8khz_mono_pcm(self):
        source_pcm = struct.pack("<" + "h" * 160, *([1000] * 160))
        source = io.BytesIO()
        with wave.open(source, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(source_pcm)

        pcm = wav_to_pcm16le_8khz(source.getvalue())

        self.assertEqual(len(pcm), 160)
        self.assertTrue(all(value == 1000 for (value,) in struct.iter_unpack("<h", pcm)))


if __name__ == "__main__":
    unittest.main()
