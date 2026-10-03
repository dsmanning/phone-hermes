from dataclasses import dataclass
import base64
import json
from typing import Callable, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class AdapterError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class UrllibTransport:
    def __init__(self, opener: Callable = urlopen) -> None:
        self._opener = opener

    def request(self, request: Request, timeout: float) -> HttpResponse:
        try:
            response = self._opener(request, timeout=timeout)
        except HTTPError as error:
            return HttpResponse(error.code, dict(error.headers.items()), error.read())
        with response:
            return HttpResponse(
                response.status,
                dict(response.headers.items()),
                response.read(),
            )


class Transport(Protocol):
    def request(self, request: Request, timeout: float) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class AgentReply:
    text: str
    response_id: str


class HomeAssistantConversationAdapter:
    def __init__(
        self,
        endpoint: str,
        host_header: str,
        transport: Transport,
        *,
        timeout: float,
    ) -> None:
        self._endpoint = endpoint
        self._host_header = host_header
        self._transport = transport
        self._timeout = timeout

    def respond(self, text: str) -> str | None:
        request = Request(
            self._endpoint,
            data=json.dumps({"text": text, "language": "en"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Host": self._host_header,
            },
            method="POST",
        )
        try:
            response = self._transport.request(request, self._timeout)
        except TimeoutError as exc:
            raise AdapterError("Home Assistant request timed out") from exc
        except OSError as exc:
            raise AdapterError("Home Assistant request failed") from exc
        if not 200 <= response.status < 300:
            raise AdapterError(f"Home Assistant request failed with HTTP {response.status}")
        try:
            result = json.loads(response.body)["response"]
            data = result.get("data") or {}
            if result.get("response_type") == "error" and data.get("code") == "no_intent_match":
                return None
            speech = result["speech"]["plain"]["speech"]
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError("Home Assistant returned an invalid response") from exc
        return str(speech).strip() or None


class HermesSpeechToTextAdapter:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        transport: Transport,
        *,
        timeout: float,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def transcribe(self, wav_audio: bytes) -> str:
        payload = {
            "data_url": "data:audio/wav;base64," + base64.b64encode(wav_audio).decode("ascii"),
            "mime_type": "audio/wav",
        }
        request = Request(
            self._endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = self._transport.request(request, self._timeout)
        except TimeoutError as exc:
            raise AdapterError("speech-to-text request timed out") from exc
        if not 200 <= response.status < 300:
            raise AdapterError(f"speech-to-text request failed with HTTP {response.status}")
        return json.loads(response.body)["transcript"]


class SpeechToTextAdapter:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        transport: Transport,
        *,
        timeout: float,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def transcribe(self, wav_audio: bytes) -> str:
        request = Request(
            self._endpoint,
            data=wav_audio,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "audio/wav",
            },
            method="POST",
        )
        try:
            response = self._transport.request(request, self._timeout)
        except TimeoutError as exc:
            raise AdapterError("speech-to-text request timed out") from exc
        if not 200 <= response.status < 300:
            raise AdapterError(f"speech-to-text request failed with HTTP {response.status}")
        return json.loads(response.body)["text"]


class TextToSpeechAdapter:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        transport: Transport,
        *,
        timeout: float,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def synthesize(self, text: str) -> bytes:
        request = Request(
            self._endpoint,
            data=json.dumps({"text": text}).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "audio/wav",
            },
            method="POST",
        )
        try:
            response = self._transport.request(request, self._timeout)
        except TimeoutError as exc:
            raise AdapterError("text-to-speech request timed out") from exc
        payload = json.loads(response.body)
        data_url = payload["data_url"]
        if not data_url.startswith("data:") or ";base64," not in data_url:
            raise AdapterError("text-to-speech response did not contain base64 audio")
        return base64.b64decode(data_url.split(",", 1)[1], validate=True)


class HermesResponsesAdapter:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        transport: Transport,
        *,
        timeout: float,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def respond(self, text: str, previous_response_id: str | None) -> AgentReply:
        payload: dict[str, object] = {
            "model": "hermes-agent",
            "input": text,
            "store": True,
        }
        if previous_response_id is not None:
            payload["previous_response_id"] = previous_response_id
        request = Request(
            self._endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = self._transport.request(request, self._timeout)
        except TimeoutError as exc:
            raise AdapterError("Hermes request timed out") from exc
        body = json.loads(response.body)
        messages = [item for item in body["output"] if item.get("type") == "message"]
        parts = [part["text"] for message in messages for part in message["content"] if part.get("type") == "output_text"]
        return AgentReply("".join(parts), body["id"])
