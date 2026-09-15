import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest
from pytest import MonkeyPatch

from x_follow_list.worker.runtime import WorkerRuntime, main


class FakeWorkerLoop:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        self.started.set()
        await stop.wait()


@pytest.mark.asyncio
async def test_worker_can_be_stopped_safely() -> None:
    runtime = WorkerRuntime()
    worker_task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0)

    assert runtime.stop_requested is False
    assert worker_task.done() is False

    runtime.request_stop()
    await asyncio.wait_for(worker_task, timeout=0.1)

    assert runtime.stop_requested is True
    assert worker_task.done() is True


@pytest.mark.asyncio
async def test_worker_runtime_delegates_to_configured_task_loop() -> None:
    loop = FakeWorkerLoop()
    runtime = WorkerRuntime(loop)

    worker_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(loop.started.wait(), timeout=0.1)
    runtime.request_stop()
    await asyncio.wait_for(worker_task, timeout=0.1)

    assert runtime.stop_requested is True


def test_worker_entrypoint_starts_async_runtime(monkeypatch: MonkeyPatch) -> None:
    called = False
    configured_levels: list[str] = []

    def fake_run(coroutine: Coroutine[Any, Any, None]) -> None:
        nonlocal called
        called = True
        coroutine.close()

    monkeypatch.setattr("x_follow_list.worker.runtime.asyncio.run", fake_run)
    monkeypatch.setattr(
        "x_follow_list.worker.runtime.configure_logging", configured_levels.append, raising=False
    )

    main()

    assert called is True
    assert configured_levels == ["INFO"]


def test_worker_entrypoint_handles_keyboard_interrupt(monkeypatch: MonkeyPatch) -> None:
    def interrupt(coroutine: Coroutine[Any, Any, None]) -> None:
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("x_follow_list.worker.runtime.asyncio.run", interrupt)

    main()
