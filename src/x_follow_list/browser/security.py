from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
from urllib.parse import urlsplit


class UnsafeEndpointError(ValueError):
    """Raised without echoing an endpoint that failed the outbound safety policy."""

    def __init__(self) -> None:
        super().__init__("provider endpoint is not permitted by policy")


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    """Validate configured provider endpoints without performing network access."""

    allowed_hosts: frozenset[str] = field(default_factory=frozenset)

    def validate(self, endpoint: str) -> str:
        try:
            parsed = urlsplit(endpoint)
            host = parsed.hostname
            _validated_port = parsed.port
        except ValueError as error:
            raise UnsafeEndpointError() from error

        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise UnsafeEndpointError()

        normalized_host = host.rstrip(".").lower()
        allowlisted = normalized_host in {
            allowed.rstrip(".").lower() for allowed in self.allowed_hosts
        }
        if allowlisted or normalized_host == "localhost":
            return endpoint

        try:
            address = ip_address(normalized_host)
        except ValueError as error:
            raise UnsafeEndpointError() from error
        if not (address.is_private or address.is_loopback):
            raise UnsafeEndpointError()
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            raise UnsafeEndpointError()
        return endpoint
