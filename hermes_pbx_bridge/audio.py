import io
import math
import struct
import wave


def pcm16le_to_wav(pcm: bytes, *, sample_rate: int = 8000) -> bytes:
    if len(pcm) % 2:
        raise ValueError("PCM payload must contain whole 16-bit samples")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def tone_pcm16le(
    *,
    frequency_hz: int,
    duration_ms: int,
    sample_rate: int = 8000,
    amplitude: int = 8000,
) -> bytes:
    sample_count = round(sample_rate * duration_ms / 1000)
    samples = (
        round(amplitude * math.sin(2 * math.pi * frequency_hz * index / sample_rate))
        for index in range(sample_count)
    )
    return struct.pack("<" + "h" * sample_count, *samples)


def wav_to_pcm16le_8khz(wav_audio: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_audio), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("TTS audio must be 16-bit mono WAV")
        sample_rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())
    if sample_rate == 8000:
        return pcm
    return _resample_pcm16le(pcm, sample_rate, 8000)


def _resample_pcm16le(pcm: bytes, input_rate: int, output_rate: int) -> bytes:
    samples = [value[0] for value in struct.iter_unpack("<h", pcm)]
    if not samples:
        return b""
    output_count = max(1, round(len(samples) * output_rate / input_rate))
    output: list[int] = []
    for index in range(output_count):
        source_position = index * input_rate / output_rate
        lower = min(int(source_position), len(samples) - 1)
        upper = min(lower + 1, len(samples) - 1)
        fraction = source_position - lower
        value = round(samples[lower] * (1 - fraction) + samples[upper] * fraction)
        output.append(max(-32768, min(32767, value)))
    return struct.pack("<" + "h" * len(output), *output)
