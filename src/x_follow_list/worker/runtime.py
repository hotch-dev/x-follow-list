import asyncio

from x_follow_list.config import Settings
from x_follow_list.observability.logging import configure_logging


class WorkerRuntime:
    """Minimal worker lifecycle used by the background process entry point."""

    def __init__(self) -> None:
        self._stop_event = asyncio.Event()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        await self._stop_event.wait()


async def _run_worker() -> None:
    runtime = WorkerRuntime()
    try:
        await runtime.run()
    finally:
        runtime.request_stop()


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    try:
        asyncio.run(_run_worker())
    except KeyboardInterrupt:
        return
