from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from x_follow_list.browser.security import EndpointPolicy

_PROVIDER_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_REFERENCE_VALUE = re.compile(r"^[A-Za-z0-9._/-]+$")
_SENSITIVE_CONFIG_KEYS = frozenset(
    {"authorization", "cookie", "password", "secret", "token", "api_key", "apikey"}
)


def validate_provider_code(code: str) -> str:
    if not _PROVIDER_CODE.fullmatch(code):
        raise ValueError("provider code must be a stable uppercase identifier")
    return code


def _contains_inline_secret(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_CONFIG_KEYS or normalized.endswith("_token"):
                return True
            if normalized.endswith("_secret") or normalized.endswith("_password"):
                return True
            if _contains_inline_secret(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_inline_secret(item) for item in value)
    return False


def _freeze_config(value: object) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("provider config keys must be strings")
            frozen[key] = _freeze_config(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_config(item) for item in value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError("provider config values must be JSON-compatible")


def _validate_config_endpoints(value: object, policy: EndpointPolicy) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower().endswith(("_url", "_endpoint")):
                if not isinstance(item, str):
                    raise ValueError("provider endpoint must be a string")
                policy.validate(item)
            _validate_config_endpoints(item, policy)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_config_endpoints(item, policy)


def _validate_secret_reference(reference: str | None) -> None:
    if reference is None:
        return
    parsed = urlsplit(reference)
    identifier = f"{parsed.netloc}{parsed.path}"
    if (
        parsed.scheme not in {"env", "keyring", "secret"}
        or not identifier
        or not _REFERENCE_VALUE.fullmatch(identifier)
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("secret reference is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class ProviderConfig:
    provider_code: str
    config_version: int
    config: Mapping[str, object] = field(default_factory=dict)
    secret_ref: str | None = None
    endpoint_policy: EndpointPolicy = field(
        default_factory=EndpointPolicy, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        validate_provider_code(self.provider_code)
        if self.config_version < 1:
            raise ValueError("provider config version must be positive")
        if _contains_inline_secret(self.config):
            raise ValueError("provider secrets must use a secret reference")
        _validate_secret_reference(self.secret_ref)
        _validate_config_endpoints(self.config, self.endpoint_policy)
        frozen = _freeze_config(self.config)
        if not isinstance(frozen, Mapping):
            raise TypeError("provider config must be a mapping")
        object.__setattr__(self, "config", frozen)

    def __repr__(self) -> str:
        return (
            "ProviderConfig("
            f"provider_code={self.provider_code!r}, config_version={self.config_version!r}, "
            "config=[REDACTED], secret_ref=[REDACTED])"
        )


@dataclass(frozen=True, slots=True)
class BrowserCapabilities:
    persistent_profiles: bool
    visible_login: bool
    remote_cdp: bool
    attach_existing_session: bool


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    provider_code: str
    provider_version: str
    capabilities: BrowserCapabilities


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    profile_ref: str
    display_name: str
    is_running: bool


@dataclass(frozen=True, slots=True)
class BrowserSessionRequest:
    config: ProviderConfig
    profile_ref: str
    owner_task_id: str
    headless: bool = False

    def __post_init__(self) -> None:
        if not self.profile_ref or not self.owner_task_id:
            raise ValueError("profile reference and owner task ID are required")


@runtime_checkable
class BrowserSession(Protocol):
    context: Any
    provider_code: str
    profile_key: str
    started_by_current_task: bool

    async def health_check(self) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class BrowserProvider(Protocol):
    code: str
    version: str
    capabilities: BrowserCapabilities

    async def validate_config(self, config: ProviderConfig) -> CapabilityReport: ...

    async def list_profiles(self, config: ProviderConfig) -> list[ProfileSummary]: ...

    async def acquire(self, request: BrowserSessionRequest) -> BrowserSession: ...


@runtime_checkable
class ManagedProfileDeletionProvider(Protocol):
    async def delete_profile(self, config: ProviderConfig, profile_ref: str) -> bool: ...
