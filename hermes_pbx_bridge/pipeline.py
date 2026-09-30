from typing import Protocol

from .adapters import AgentReply
from .audio import pcm16le_to_wav, wav_to_pcm16le_8khz
from .sessions import CallSession


class SpeechToText(Protocol):
    def transcribe(self, wav_audio: bytes) -> str: ...


class Agent(Protocol):
    def respond(self, text: str, previous_response_id: str | None) -> AgentReply: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> bytes: ...


class CallProcessor:
    def __init__(self, stt: SpeechToText, agent: Agent, tts: TextToSpeech) -> None:
        self._stt = stt
        self._agent = agent
        self._tts = tts

    def process_turn(self, session: CallSession, pcm: bytes) -> bytes:
        text = self._stt.transcribe(pcm16le_to_wav(pcm))
        reply = self._agent.respond(text, session.previous_response_id)
        session.previous_response_id = reply.response_id
        return wav_to_pcm16le_8khz(self._tts.synthesize(reply.text))
