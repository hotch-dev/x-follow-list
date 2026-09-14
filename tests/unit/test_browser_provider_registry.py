import pytest

from x_follow_list.browser.contracts import ProviderConfig
from x_follow_list.browser.fake import FakeBrowserProvider
from x_follow_list.browser.registry import (
    BrowserProviderRegistry,
    DuplicateProviderError,
    ProviderNotFoundError,
)


def test_registry_resolves_stable_provider_codes_and_lists_capabilities() -> None:
    provider = FakeBrowserProvider()
    registry = BrowserProviderRegistry([provider])

    assert registry.get("FAKE") is provider
    assert registry.descriptors()[0].code == "FAKE"
    assert registry.descriptors()[0].capabilities == provider.capabilities


def test_registry_rejects_duplicate_or_unknown_provider_codes() -> None:
    registry = BrowserProviderRegistry([FakeBrowserProvider()])

    with pytest.raises(DuplicateProviderError):
        registry.register(FakeBrowserProvider())
    with pytest.raises(ProviderNotFoundError) as caught:
        registry.get("MISSING")

    assert "MISSING" not in str(caught.value)


@pytest.mark.parametrize("code", ["fake", "FAKE PROVIDER", "ADSPOWER/../../x"])
def test_registry_rejects_unstable_provider_codes(code: str) -> None:
    provider = FakeBrowserProvider()
    provider.code = code

    with pytest.raises(ValueError, match="provider code"):
        BrowserProviderRegistry([provider])


def test_config_is_versioned_and_does_not_accept_inline_secrets() -> None:
    config = ProviderConfig(
        provider_code="FAKE",
        config_version=2,
        config={"api_url": "http://localhost:50325"},
        secret_ref="env://FAKE_PROVIDER_TOKEN",
    )

    assert config.config_version == 2
    assert config.secret_ref == "env://FAKE_PROVIDER_TOKEN"
    with pytest.raises(ValueError, match="secret reference"):
        ProviderConfig(
            provider_code="FAKE",
            config_version=1,
            config={"api_token": "inline-secret"},
            secret_ref=None,
        )
