import logging
import os

from .adapters import HermesResponsesAdapter, Transport, UrllibTransport
from .config import Config
from .pipeline import CallProcessor
from .server import AudioSocketServer
from .turns import TurnDetector
from .wyoming import SocketEventClient, WyomingSpeechToTextAdapter, WyomingTextToSpeechAdapter


def build_server(config: Config, *, transport: Transport) -> AudioSocketServer:
    stt = WyomingSpeechToTextAdapter(
        lambda: SocketEventClient(*config.wyoming_stt_address, 60)
    )
    agent = HermesResponsesAdapter(
        config.responses_url, config.api_key, transport, timeout=180
    )
    tts = WyomingTextToSpeechAdapter(
        lambda: SocketEventClient(*config.wyoming_tts_address, 60)
    )
    processor = CallProcessor(stt, agent, tts)
    return AudioSocketServer(
        config.bind_address,
        allowed_sources=config.allowed_sources,
        processor=processor,
        detector_factory=lambda: TurnDetector(
            threshold=200,
            minimum_speech_ms=300,
            trailing_silence_ms=1200,
            maximum_turn_ms=120_000,
            frame_ms=20,
        ),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.from_environment(os.environ)
    server = build_server(config, transport=UrllibTransport())
    logging.info("AudioSocket bridge listening on %s:%d", *server.server_address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
