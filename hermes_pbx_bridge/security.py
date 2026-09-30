from collections.abc import Iterable
from ipaddress import IPv4Address
from uuid import UUID


class ConnectionGuard:
    def __init__(self, allowed_sources: Iterable[str]) -> None:
        self._allowed_sources = frozenset(IPv4Address(address) for address in allowed_sources)
        if not self._allowed_sources:
            raise ValueError("at least one source address must be allowed")

    def authorize_source(self, address: str) -> None:
        try:
            source = IPv4Address(address)
        except ValueError as exc:
            raise PermissionError("invalid source address") from exc
        if source not in self._allowed_sources:
            raise PermissionError("source address is not allowed")

    def parse_call_uuid(self, payload: bytes) -> UUID:
        if len(payload) != 16:
            raise ValueError("call UUID must contain exactly 16 bytes")
        call_id = UUID(bytes=payload)
        if call_id.int == 0:
            raise ValueError("call UUID must not be nil")
        return call_id
