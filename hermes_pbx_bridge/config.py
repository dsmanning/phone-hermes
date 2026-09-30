from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Config:
    bind_host: str
    port: int
    allowed_sources: frozenset[str]
    api_base_url: str
    api_key: str
    wyoming_stt_host: str = "calliope.local"
    wyoming_stt_port: int = 10300
    wyoming_tts_host: str = "calliope.local"
    wyoming_tts_port: int = 10200

    @property
    def bind_address(self) -> tuple[str, int]:
        return self.bind_host, self.port

    @property
    def wyoming_stt_address(self) -> tuple[str, int]:
        return self.wyoming_stt_host, self.wyoming_stt_port

    @property
    def wyoming_tts_address(self) -> tuple[str, int]:
        return self.wyoming_tts_host, self.wyoming_tts_port

    @property
    def responses_url(self) -> str:
        return f"{self.api_base_url}/v1/responses"

    @property
    def transcribe_url(self) -> str:
        return f"{self.api_base_url}/api/audio/transcribe"

    @property
    def speak_url(self) -> str:
        return f"{self.api_base_url}/api/audio/speak"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "Config":
        api_key = environment.get("HERMES_API_KEY", "").strip()
        if not api_key:
            raise ValueError("HERMES_API_KEY is required")
        allowed = frozenset(
            value.strip()
            for value in environment.get("PBX_BRIDGE_ALLOWED_SOURCES", "").split(",")
            if value.strip()
        )
        if not allowed:
            raise ValueError("PBX_BRIDGE_ALLOWED_SOURCES is required")
        return cls(
            bind_host=environment.get("PBX_BRIDGE_BIND", "0.0.0.0"),
            port=int(environment.get("PBX_BRIDGE_PORT", "8443")),
            allowed_sources=allowed,
            api_base_url=environment.get("HERMES_API_BASE_URL", "http://127.0.0.1:8642").rstrip("/"),
            api_key=api_key,
            wyoming_stt_host=environment.get("WYOMING_STT_HOST", "calliope.local"),
            wyoming_stt_port=int(environment.get("WYOMING_STT_PORT", "10300")),
            wyoming_tts_host=environment.get("WYOMING_TTS_HOST", "calliope.local"),
            wyoming_tts_port=int(environment.get("WYOMING_TTS_PORT", "10200")),
        )
