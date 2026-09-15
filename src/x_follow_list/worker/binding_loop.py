from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from x_follow_list.application.binding import BindingClaim


class BindingQueue(Protocol):
    async def expire_due(self) -> list[str]: ...

    async def claim_next(self, worker_id: str) -> BindingClaim | None: ...


BindingHandler = Callable[[BindingClaim], Awaitable[None]]


class BindingWorker:
    def __init__(
        self,
        service: BindingQueue,
        worker_id: str,
        handler: BindingHandler,
        *,
        idle_poll_interval: float = 1.0,
    ) -> None:
        if idle_poll_interval <= 0:
            raise ValueError("worker interval must be positive")
        self._service = service
        self._worker_id = worker_id
        self._handler = handler
        self._idle_poll_interval = idle_poll_interval

    async def startup(self) -> list[str]:
        return await self._service.expire_due()

    async def run_once(self) -> bool:
        claim = await self._service.claim_next(self._worker_id)
        if claim is None:
            return False
        await self._handler(claim)
        return True

    async def run(self, stop: asyncio.Event) -> None:
        await self.startup()
        while not stop.is_set():
            if await self.run_once():
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._idle_poll_interval)
            except TimeoutError:
                continue
