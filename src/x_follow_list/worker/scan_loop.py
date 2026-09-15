from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from x_follow_list.application.scan_coordination import (
    LeaseConflictError,
    LeaseLostError,
    ResourceLease,
    ScanClaim,
)


class Coordinator(Protocol):
    async def recover_expired_runs(self) -> list[str]: ...

    async def claim_next(self, worker_id: str) -> ScanClaim | None: ...

    async def acquire_scan_leases(self, claim: ScanClaim) -> tuple[ResourceLease, ...]: ...

    async def finish_unleased_failed(
        self, claim: ScanClaim, error_code: str, error_summary: str
    ) -> None: ...

    async def finish_failed(
        self,
        claim: ScanClaim,
        leases: tuple[ResourceLease, ...],
        error_code: str,
        error_summary: str,
    ) -> None: ...

    async def heartbeat(
        self, claim: ScanClaim, leases: tuple[ResourceLease, ...]
    ) -> None: ...


JobHandler = Callable[[ScanClaim, tuple[ResourceLease, ...]], Coroutine[Any, Any, None]]
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class ScanWorker:
    """Poll durable work and cancel the active job immediately when its lease is lost."""

    def __init__(
        self,
        coordinator: Coordinator,
        worker_id: str,
        handler: JobHandler,
        *,
        heartbeat_interval: float = 10.0,
        idle_poll_interval: float = 1.0,
    ) -> None:
        if heartbeat_interval <= 0 or idle_poll_interval <= 0:
            raise ValueError("worker intervals must be positive")
        self._coordinator = coordinator
        self._worker_id = worker_id
        self._handler = handler
        self._heartbeat_interval = heartbeat_interval
        self._idle_poll_interval = idle_poll_interval

    async def startup(self) -> list[str]:
        return await self._coordinator.recover_expired_runs()

    async def run_once(self) -> bool:
        claim = await self._coordinator.claim_next(self._worker_id)
        if claim is None:
            return False
        try:
            leases = await self._coordinator.acquire_scan_leases(claim)
        except LeaseConflictError:
            await self._coordinator.finish_unleased_failed(
                claim,
                "RESOURCE_BUSY",
                "required browser resource is already in use",
            )
            return True
        job: asyncio.Task[None] = asyncio.create_task(self._handler(claim, leases))
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(job), timeout=self._heartbeat_interval
                    )
                    return True
                except TimeoutError:
                    await self._coordinator.heartbeat(claim, leases)
                except Exception as error:
                    error_code = getattr(error, "code", "SCAN_FAILED")
                    if not isinstance(error_code, str) or not _SAFE_ERROR_CODE.fullmatch(
                        error_code
                    ):
                        error_code = "SCAN_FAILED"
                    try:
                        await self._coordinator.finish_failed(
                            claim, leases, error_code, "scan execution failed"
                        )
                    except LeaseLostError:
                        pass
                    return True
        except BaseException:
            job.cancel()
            try:
                await job
            except asyncio.CancelledError:
                pass
            raise

    async def run(self, stop: asyncio.Event) -> None:
        await self.startup()
        while not stop.is_set():
            if await self.run_once():
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._idle_poll_interval)
            except TimeoutError:
                continue
