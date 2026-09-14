import httpx
import pytest
from pytest import MonkeyPatch

from x_follow_list.api.app import create_app, main


@pytest.mark.asyncio
async def test_liveness_reports_service_identity() -> None:
    transport = httpx.ASGITransport(app=create_app())

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"service": "x-follow-list-api", "status": "ok"}


def test_api_entrypoint_starts_the_app_factory(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    configured_levels: list[str] = []

    def fake_run(app: str, **options: object) -> None:
        captured["app"] = app
        captured.update(options)

    monkeypatch.setattr("x_follow_list.api.app.uvicorn.run", fake_run)
    monkeypatch.setattr(
        "x_follow_list.api.app.configure_logging", configured_levels.append, raising=False
    )

    main()

    assert captured == {
        "app": "x_follow_list.api.app:create_app",
        "factory": True,
        "host": "127.0.0.1",
        "port": 8000,
    }
    assert configured_levels == ["INFO"]


@pytest.mark.asyncio
async def test_api_propagates_a_safe_request_correlation_id() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live", headers={"X-Request-ID": "request-123"})

    await app.state.database.dispose()
    assert response.headers["X-Request-ID"] == "request-123"


@pytest.mark.asyncio
async def test_api_replaces_an_unsafe_request_correlation_id() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live", headers={"X-Request-ID": "bad\nvalue"})

    await app.state.database.dispose()
    assert response.headers["X-Request-ID"] != "bad\nvalue"
    assert len(response.headers["X-Request-ID"]) == 36
