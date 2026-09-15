import asyncio

import pytest

from x_follow_list.application.scan_coordination import (
    LeaseConflictError,
    LeaseLostError,
    ResourceLease,
    ScanClaim,
)
from x_follow_list.worker.scan_loop import ScanWorker


class FakeCoordinator:
    def __init__(self, lose_heartbeat: bool = False, lease_conflict: bool = False) -> None:
        self.claim = ScanClaim("run", "account", "worker", "claim")
        self.leases = (
            ResourceLease("x-account:account", 1),
            ResourceLease("browser-profile:provider:profile", 1),
        )
        self.heartbeats = 0
        self.recoveries = 0
        self.lose_heartbeat = lose_heartbeat
        self.lease_conflict = lease_conflict
        self.unleased_failure: tuple[str, str] | None = None
        self.failure: tuple[str, str] | None = None
        self.two_heartbeats = asyncio.Event()

    async def recover_expired_runs(self) -> list[str]:
        self.recoveries += 1
        return []

    async def claim_next(self, worker_id: str) -> ScanClaim | None:
        assert worker_id == "worker"
        value, self.claim = self.claim, None  # type: ignore[assignment]
        return value

    async def acquire_scan_leases(self, claim: ScanClaim) -> tuple[ResourceLease, ...]:
        assert claim.run_id == "run"
        if self.lease_conflict:
            raise LeaseConflictError("busy")
        return self.leases

    async def finish_unleased_failed(
        self, claim: ScanClaim, error_code: str, error_summary: str
    ) -> None:
        assert claim.run_id == "run"
        self.unleased_failure = (error_code, error_summary)

    async def finish_failed(
        self,
        claim: ScanClaim,
        leases: tuple[ResourceLease, ...],
        error_code: str,
        error_summary: str,
    ) -> None:
        assert claim.run_id == "run" and leases == self.leases
        self.failure = (error_code, error_summary)

    async def heartbeat(
        self, claim: ScanClaim, leases: tuple[ResourceLease, ...]
    ) -> None:
        assert claim.run_id == "run" and leases == self.leases
        self.heartbeats += 1
        if self.heartbeats >= 2:
            self.two_heartbeats.set()
        if self.lose_heartbeat:
            raise LeaseLostError("lost")


@pytest.mark.asyncio
async def test_worker_recovers_on_start_and_heartbeats_while_job_runs() -> None:
    coordinator = FakeCoordinator()
    handled = asyncio.Event()

    async def handle(_claim: ScanClaim, _leases: tuple[ResourceLease, ...]) -> None:
        await coordinator.two_heartbeats.wait()
        handled.set()

    worker = ScanWorker(coordinator, "worker", handle, heartbeat_interval=0.001)
    await worker.startup()
    assert await worker.run_once() is True

    assert coordinator.recoveries == 1
    assert coordinator.heartbeats >= 2
    assert handled.is_set()


@pytest.mark.asyncio
async def test_worker_cancels_inflight_navigation_when_heartbeat_is_lost() -> None:
    coordinator = FakeCoordinator(lose_heartbeat=True)
    cancelled = asyncio.Event()

    async def handle(_claim: ScanClaim, _leases: tuple[ResourceLease, ...]) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    worker = ScanWorker(coordinator, "worker", handle, heartbeat_interval=0.001)
    with pytest.raises(LeaseLostError):
        await worker.run_once()

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_worker_safely_closes_claim_when_resources_are_busy() -> None:
    coordinator = FakeCoordinator(lease_conflict=True)

    async def handle(_claim: ScanClaim, _leases: tuple[ResourceLease, ...]) -> None:
        raise AssertionError("handler must not start without both leases")

    worker = ScanWorker(coordinator, "worker", handle)

    assert await worker.run_once() is True
    assert coordinator.unleased_failure == (
        "RESOURCE_BUSY",
        "required browser resource is already in use",
    )


@pytest.mark.asyncio
async def test_worker_records_sanitized_handler_failure_and_remains_available() -> None:
    coordinator = FakeCoordinator()

    class UnsafeFailure(RuntimeError):
        code = "unsafe/code"

    async def fail(_claim: ScanClaim, _leases: tuple[ResourceLease, ...]) -> None:
        raise UnsafeFailure("secret diagnostic must not be persisted")

    worker = ScanWorker(coordinator, "worker", fail)

    assert await worker.run_once() is True
    assert coordinator.failure == ("SCAN_FAILED", "scan execution failed")
    assert await worker.run_once() is False
