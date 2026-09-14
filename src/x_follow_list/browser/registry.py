from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from x_follow_list.browser.contracts import (
    BrowserCapabilities,
    BrowserProvider,
    validate_provider_code,
)


class DuplicateProviderError(ValueError):
    def __init__(self) -> None:
        super().__init__("provider code is already registered")


class ProviderNotFoundError(LookupError):
    def __init__(self) -> None:
        super().__init__("browser provider is not registered")


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    code: str
    version: str
    capabilities: BrowserCapabilities


class BrowserProviderRegistry:
    def __init__(self, providers: Iterable[BrowserProvider] = ()) -> None:
        self._providers: dict[str, BrowserProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: BrowserProvider) -> None:
        if not isinstance(provider, BrowserProvider):
            raise TypeError("provider does not implement the browser provider contract")
        code = validate_provider_code(provider.code)
        if code in self._providers:
            raise DuplicateProviderError()
        self._providers[code] = provider

    def get(self, code: str) -> BrowserProvider:
        try:
            return self._providers[code]
        except KeyError as error:
            raise ProviderNotFoundError() from error

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(
            ProviderDescriptor(provider.code, provider.version, provider.capabilities)
            for provider in sorted(self._providers.values(), key=lambda item: item.code)
        )
