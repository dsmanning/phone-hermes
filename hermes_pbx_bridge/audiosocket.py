from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Frame:
    kind: int
    payload: bytes


class FrameDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while len(self._buffer) >= 3:
            length = int.from_bytes(self._buffer[1:3], "big")
            frame_length = 3 + length
            if len(self._buffer) < frame_length:
                break
            frames.append(Frame(self._buffer[0], bytes(self._buffer[3:frame_length])))
            del self._buffer[:frame_length]
        return frames


def encode_frame(frame: Frame) -> bytes:
    if not 0 <= frame.kind <= 0xFF:
        raise ValueError("frame kind must fit in one byte")
    if len(frame.payload) > 0xFFFF:
        raise ValueError("payload exceeds AudioSocket frame limit")
    return bytes((frame.kind,)) + len(frame.payload).to_bytes(2, "big") + frame.payload
