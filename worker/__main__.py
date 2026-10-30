"""The run worker (Phase 5.6a): `python -m worker`, its own service in Docker Compose.

Claims runs from the Postgres queue and executes them (orchestrator/runner.py). It needs
Postgres in every environment: in-memory runs live inside the API process, which runs its own
worker in development. SIGTERM (a redeploy) hands running runs back to the queue.
"""
import asyncio
import logging
import signal

from api.config import settings
from orchestrator.runner import open_deps, work

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


async def main() -> None:
    if not settings.encryption_key:
        raise SystemExit("ENCRYPTION_KEY is not set: checkpoints can't be stored safely.")
    deps = await open_deps("production", settings.database_url)   # no in-memory fallback
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    logging.getLogger(__name__).info("worker started (concurrency %d)",
                                     settings.worker_concurrency)
    try:
        await work(deps, stop, concurrency=settings.worker_concurrency)
    finally:
        for close in deps.closers:
            await close()


if __name__ == "__main__":
    asyncio.run(main())
