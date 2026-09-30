from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class CallSession:
    call_id: UUID
    previous_response_id: str | None = None


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[UUID, CallSession] = {}

    def open(self, call_id: UUID) -> CallSession:
        session = self._sessions.get(call_id)
        if session is None:
            session = CallSession(call_id)
            self._sessions[call_id] = session
        return session

    def close(self, call_id: UUID) -> None:
        self._sessions.pop(call_id, None)
