import math
import struct


class TurnDetector:
    def __init__(
        self,
        *,
        threshold: int,
        minimum_speech_ms: int,
        trailing_silence_ms: int,
        frame_ms: int,
        maximum_turn_ms: int = 120_000,
    ) -> None:
        self._threshold = threshold
        self._minimum_speech_frames = math.ceil(minimum_speech_ms / frame_ms)
        self._trailing_silence_frames = math.ceil(trailing_silence_ms / frame_ms)
        self._maximum_frames = math.ceil(maximum_turn_ms / frame_ms)
        self._audio = bytearray()
        self._speech_frames = 0
        self._silence_frames = 0
        self._turn_frames = 0

    def feed(self, pcm: bytes) -> bytes | None:
        if len(pcm) % 2:
            raise ValueError("PCM payload must contain whole 16-bit samples")
        samples = tuple(sample[0] for sample in struct.iter_unpack("<h", pcm))
        rms = math.isqrt(sum(value * value for value in samples) // max(1, len(samples)))
        is_speech = rms >= self._threshold

        if is_speech:
            self._audio.extend(pcm)
            self._speech_frames += 1
            self._turn_frames += 1
            self._silence_frames = 0
            if self._turn_frames >= self._maximum_frames:
                turn = bytes(self._audio)
                self.reset()
                return turn
            return None

        if self._speech_frames:
            self._audio.extend(pcm)
            self._turn_frames += 1
            self._silence_frames += 1
            if self._silence_frames >= self._trailing_silence_frames:
                if self._speech_frames >= self._minimum_speech_frames:
                    turn = bytes(self._audio)
                    self.reset()
                    return turn
                self.reset()
        return None

    def reset(self) -> None:
        self._audio.clear()
        self._speech_frames = 0
        self._silence_frames = 0
        self._turn_frames = 0
