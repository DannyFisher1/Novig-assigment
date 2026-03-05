"""
Replica service entry point.

Subscribes to Core's SSE stream for live updates.
Re-syncs from snapshot on each reconnect.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router
from .state_store import store
from .subscriber import start_sse_subscriber
from .config import HEARTBEAT_INTERVAL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)



async def heartbeat_loop():
    """Log replica status periodically."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)

        ## get the lag, status, and errors
        lag = time.time() - store.last_event_ts if store.last_event_ts > 0 else None
        lag_str = store.get_lag_str(lag)
        status = store.get_status_str(store.connected)
        errors = store.get_errors_str(store.errors)

        logger.info(
            "[HEARTBEAT] %s seq=%d races=%d lag=%s errors=%d",
            status,
            store.last_sequence_id,
            len(store.live_races),
            lag_str,
            errors,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[REPLICA] Starting...")

    # start the sse subscriber and heartbeat loop
    subscriber_task = asyncio.create_task(start_sse_subscriber())
    heartbeat_task = asyncio.create_task(heartbeat_loop())

    yield

    logger.info("[REPLICA] Shutting down...")
    heartbeat_task.cancel()
    subscriber_task.cancel()


app = FastAPI(title="Horse Racing Replica", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
