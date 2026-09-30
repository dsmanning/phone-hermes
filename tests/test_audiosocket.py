import unittest

from hermes_pbx_bridge.audiosocket import Frame, FrameDecoder, encode_frame


class AudioSocketFramingTests(unittest.TestCase):
    def test_decodes_fragmented_frame_and_preserves_following_frame(self):
        decoder = FrameDecoder()

        self.assertEqual(decoder.feed(b"\x01\x00"), [])
        frames = decoder.feed(b"\x10" + bytes(range(16)) + b"\x10\x00\x02\x01\x02")

        self.assertEqual(
            frames,
            [Frame(0x01, bytes(range(16))), Frame(0x10, b"\x01\x02")],
        )

    def test_encodes_big_endian_payload_length(self):
        payload = bytes(range(256)) + b"x"

        encoded = encode_frame(Frame(0x10, payload))

        self.assertEqual(encoded[:3], b"\x10\x01\x01")
        self.assertEqual(encoded[3:], payload)


if __name__ == "__main__":
    unittest.main()
