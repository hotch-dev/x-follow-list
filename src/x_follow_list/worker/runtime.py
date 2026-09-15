import asyncio
import os
import socket
from typing import Protocol

from x_follow_list.application.scan_coordination import ScanCoordinator
from x_follow_list.artifacts.xlsx import XlsxArtifactService
from x_follow_list.browser.direct_chrome import DirectChromeProvider
from x_follow_list.browser.registry import BrowserProviderRegistry
from x_follow_list.config import Settings
from x_follow_list.observability.logging import configure_logging
from x_follow_list.persistence.database import Database
from x_follow_list.worker.configured_scan import ConfiguredScanJob
from x_follow_list.worker.relationship_scan import RelationshipScanJob
from x_follow_list.worker.scan_loop import ScanWorker


class TaskLoop(Protocol):
    async def run(self, stop: asyncio.Event) -> None: ...


class WorkerRuntime:
    """Own the stop signal and delegate work to the configured durable loop."""

    def __init__(self, task_loop: TaskLoop | None = None) -> None:
        self._stop_event = asyncio.Event()
        self._task_loop = task_loop

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        if self._task_loop is None:
            await self._stop_event.wait()
            return
        await self._task_loop.run(self._stop_event)


async def _run_worker(settings: Settings) -> None:
    database = Database.from_settings(settings)
    coordinator = ScanCoordinator(database)
    registry = BrowserProviderRegistry([DirectChromeProvider()])
    artifacts = XlsxArtifactService(
        database, settings.data_dir / "artifacts", settings.display_timezone
    )
    handler = ConfiguredScanJob(
        database,
        registry,
        lambda profile_url: RelationshipScanJob(
            database, coordinator, artifacts, profile_url
        ),
    )
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    runtime = WorkerRuntime(ScanWorker(coordinator, worker_id, handler))
    try:
        await runtime.run()
    finally:
        runtime.request_stop()
        await database.dispose()


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    try:
        asyncio.run(_run_worker(settings))
    except KeyboardInterrupt:
        return
