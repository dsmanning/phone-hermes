import logging
from collections.abc import Callable
from typing import Protocol

from .adapters import AdapterError, AgentReply
from .audio import pcm16le_to_wav, tone_pcm16le, wav_to_pcm16le_8khz
from .sessions import CallSession


logger = logging.getLogger(__name__)


class SpeechToText(Protocol):
    def transcribe(self, wav_audio: bytes) -> str: ...


class Agent(Protocol):
    def respond(self, text: str, previous_response_id: str | None) -> AgentReply: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> bytes: ...


class HomeAutomation(Protocol):
    def respond(self, text: str) -> str | None: ...


class CallProcessor:
    def __init__(
        self,
        stt: SpeechToText,
        agent: Agent,
        tts: TextToSpeech,
        *,
        home: HomeAutomation | None = None,
    ) -> None:
        self._stt = stt
        self._agent = agent
        self._tts = tts
        self._home = home

    def begin_call(self, session: CallSession) -> bytes:
        speech = wav_to_pcm16le_8khz(self._tts.synthesize("Chiron. Command."))
        audio = bytes(8000 * 2) + speech
        logger.info("Opening prompt synthesis completed")
        return audio

    def end_of_command(self) -> bytes:
        return tone_pcm16le(frequency_hz=1000, duration_ms=150)

    def process_turn(
        self,
        session: CallSession,
        pcm: bytes,
        progress: Callable[[bytes], None] | None = None,
    ) -> bytes:
        text = self._stt.transcribe(pcm16le_to_wav(pcm))
        logger.info("Speech-to-text completed")
        command = text.strip()
        spoken_command = command.rstrip(".?!")
        if progress is not None:
            progress_audio = wav_to_pcm16le_8khz(
                self._tts.synthesize(f"You said: {spoken_command}. Please wait.")
            )
            progress(progress_audio)
            logger.info("Command acknowledgement completed")
        if self._home is not None:
            try:
                home_reply = self._home.respond(command)
            except AdapterError as exc:
                logger.warning("Home Assistant unavailable error=%s", type(exc).__name__)
            else:
                if home_reply is not None:
                    logger.info("Home Assistant handled command")
                    audio = wav_to_pcm16le_8khz(self._tts.synthesize(home_reply))
                    logger.info("Text-to-speech completed")
                    return audio
                logger.info("Home Assistant did not match command; using Hermes fallback")
        agent_input = (
            f"Telephone command: {command}\n"
            "Execute it now. Reply with one concise spoken sentence stating whether it "
            "succeeded. Never claim success unless a tool result confirms it."
        )
        reply = self._agent.respond(agent_input, session.previous_response_id)
        logger.info("Hermes response completed")
        session.previous_response_id = reply.response_id
        audio = wav_to_pcm16le_8khz(self._tts.synthesize(reply.text))
        logger.info("Text-to-speech completed")
        return audio
