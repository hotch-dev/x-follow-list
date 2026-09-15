import asyncio

import pytest

from x_follow_list.application.binding import BindingClaim
from x_follow_list.worker.binding_loop import BindingWorker


class FakeBindingService:
    def __init__(self) -> None:
        self.claim: BindingClaim | None = BindingClaim("binding", "worker", "token")
        self.expirations = 0

    async def expire_due(self) -> list[str]:
        self.expirations += 1
        return ["expired"]

    async def claim_next(self, worker_id: str) -> BindingClaim | None:
        assert worker_id == "worker"
        claim, self.claim = self.claim, None
        return claim


@pytest.mark.asyncio
async def test_binding_worker_expires_stale_sessions_and_processes_claims() -> None:
    service = FakeBindingService()
    handled: list[str] = []

    async def handle(claim: BindingClaim) -> None:
        handled.append(claim.session_id)

    worker = BindingWorker(service, "worker", handle, idle_poll_interval=0.001)

    assert await worker.startup() == ["expired"]
    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert handled == ["binding"]

    stop = asyncio.Event()
    stop.set()
    await worker.run(stop)
    assert service.expirations == 2
