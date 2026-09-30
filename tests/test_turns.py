import struct
import unittest

from hermes_pbx_bridge.turns import TurnDetector


def pcm_frame(amplitude, samples=160):
    return b"".join(struct.pack("<h", amplitude) for _ in range(samples))


class TurnDetectorTests(unittest.TestCase):
    def test_emits_speech_after_configured_trailing_silence(self):
        detector = TurnDetector(
            threshold=200,
            minimum_speech_ms=60,
            trailing_silence_ms=40,
            frame_ms=20,
        )
        speech = pcm_frame(1000)
        silence = pcm_frame(0)

        self.assertIsNone(detector.feed(silence))
        self.assertIsNone(detector.feed(speech))
        self.assertIsNone(detector.feed(speech))
        self.assertIsNone(detector.feed(speech))
        self.assertIsNone(detector.feed(silence))
        turn = detector.feed(silence)

        self.assertEqual(turn, speech * 3 + silence * 2)

    def test_emits_when_maximum_turn_duration_is_reached(self):
        detector = TurnDetector(
            threshold=200,
            minimum_speech_ms=20,
            trailing_silence_ms=200,
            maximum_turn_ms=60,
            frame_ms=20,
        )
        speech = pcm_frame(1000)

        self.assertIsNone(detector.feed(speech))
        self.assertIsNone(detector.feed(speech))
        self.assertEqual(detector.feed(speech), speech * 3)


if __name__ == "__main__":
    unittest.main()
