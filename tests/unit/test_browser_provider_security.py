import logging

import pytest

from x_follow_list.browser.contracts import ProviderConfig
from x_follow_list.browser.security import EndpointPolicy, UnsafeEndpointError
from x_follow_list.observability.logging import SecretRedactionFilter


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:50325",
        "http://localhost:50325",
        "https://10.20.30.40/provider",
        "https://192.168.1.20/provider",
    ],
)
def test_default_endpoint_policy_allows_only_local_or_private_endpoints(endpoint: str) -> None:
    policy = EndpointPolicy()

    assert policy.validate(endpoint) == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://127.0.0.1/provider",
        "https://user:password@127.0.0.1/provider",
        "https://8.8.8.8/provider",
        "https://example.com/provider",
        "https://127.0.0.1/provider#secret",
        "https://127.0.0.1/provider?token=secret",
    ],
)
def test_default_endpoint_policy_rejects_unsafe_endpoints(endpoint: str) -> None:
    with pytest.raises(UnsafeEndpointError):
        EndpointPolicy().validate(endpoint)


def test_explicit_allowlist_accepts_a_named_provider_host() -> None:
    policy = EndpointPolicy(allowed_hosts=frozenset({"provider.internal"}))

    assert policy.validate("https://provider.internal/api") == "https://provider.internal/api"


def test_provider_config_uses_its_administrator_endpoint_policy() -> None:
    config = ProviderConfig(
        provider_code="FAKE",
        config_version=1,
        config={"api_url": "https://provider.internal/api"},
        secret_ref="env://FAKE_PROVIDER_TOKEN",
        endpoint_policy=EndpointPolicy(allowed_hosts=frozenset({"provider.internal"})),
    )

    assert config.config["api_url"] == "https://provider.internal/api"


def test_provider_config_cannot_be_mutated_after_safety_validation() -> None:
    raw_config: dict[str, object] = {
        "api_url": "http://127.0.0.1:50325",
        "options": {"timeout": 5},
    }
    config = ProviderConfig(
        provider_code="FAKE",
        config_version=1,
        config=raw_config,
        secret_ref="env://FAKE_PROVIDER_TOKEN",
    )

    raw_config["api_token"] = "late-inline-secret"
    nested = raw_config["options"]
    assert isinstance(nested, dict)
    nested["password"] = "late-password"

    assert "api_token" not in config.config
    assert config.config["options"] == {"timeout": 5}


def test_config_and_provider_diagnostics_are_redacted_from_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "provider-token-value"
    cdp_url = "ws://127.0.0.1/devtools/browser/private-id"
    config = ProviderConfig(
        provider_code="FAKE",
        config_version=1,
        config={"api_url": "http://127.0.0.1:50325"},
        secret_ref="env://FAKE_PROVIDER_TOKEN",
    )
    logger = logging.getLogger("provider-contract-test")
    provider_filter = SecretRedactionFilter()
    caplog.handler.addFilter(provider_filter)
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            logger.info(
                "provider failure token=%s",
                secret,
                extra={"cdp_url": cdp_url, "provider_config": config},
            )
    finally:
        caplog.handler.removeFilter(provider_filter)

    rendered = caplog.text
    assert secret not in rendered
    assert cdp_url not in rendered


def test_unsafe_endpoint_error_does_not_echo_the_endpoint() -> None:
    endpoint = "https://public-provider.example/private-token"

    with pytest.raises(UnsafeEndpointError) as caught:
        ProviderConfig(
            provider_code="FAKE",
            config_version=1,
            config={"api_url": endpoint},
            secret_ref="env://FAKE_PROVIDER_TOKEN",
        )

    assert endpoint not in str(caught.value)


def test_nested_provider_endpoint_cannot_bypass_safety_validation() -> None:
    with pytest.raises(UnsafeEndpointError):
        ProviderConfig(
            provider_code="FAKE",
            config_version=1,
            config={"network": {"api_url": "https://8.8.8.8/provider"}},
            secret_ref="env://FAKE_PROVIDER_TOKEN",
        )
