"""
Core service entry point.

Initializes the race simulation and starts the main loop.

Usage:
    uvicorn core.app:app --port 8000 --timeout-graceful-shutdown 2
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from .race_manager import RaceManager
from .routes import router
from .seeding import generate_game_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Shutdown signal for SSE streams
shutdown_event = asyncio.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[CORE] Starting...")
    shutdown_event.clear()

    ## generate the world data
    horses, venues = generate_game_data()
    ## create the race manager
    manager = RaceManager(horses, venues)

    ## set the state on the app
    app.state.manager = manager
    app.state.shutdown_event = shutdown_event

    ## start the main loop
    manager_task = asyncio.create_task(manager.main_loop())

    yield

    logger.info("[CORE] Shutting down...")
    shutdown_event.set()  # Signal SSE streams to close

    for task in manager.active_tasks.values():
        task.cancel()
    if manager.active_tasks:
        await asyncio.wait_for(
            asyncio.gather(*manager.active_tasks.values(), return_exceptions=True),
            timeout=2.0
        )
    manager_task.cancel()
    with suppress(asyncio.CancelledError):
        await manager_task


app = FastAPI(title="Horse Racing Core", lifespan=lifespan)
app.include_router(router)
