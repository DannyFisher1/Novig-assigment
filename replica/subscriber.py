"""
SSE subscriber: connects to Core and applies events to local state.

On disconnect, re-syncs from snapshot (simple approach - no gap healing).
"""

import asyncio
import json
import logging
import random
import time

import httpx
from httpx_sse import aconnect_sse

from .config import CORE_URL
from .state_store import store

logger = logging.getLogger(__name__)


async def sync_snapshot():
    """Fetch full state snapshot from Core."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CORE_URL}/replication/snapshot", timeout=10.0)
        resp.raise_for_status()
        await store.apply_snapshot(resp.json())
        logger.info("[SSE] Synced snapshot, seq=%d", store.last_sequence_id)


async def start_sse_subscriber():
    """Connect to Core's SSE stream with auto-reconnect."""
    backoff = 1
    max_backoff = 30

    while True:
        try:
            # Always sync fresh on connect to avoid gaps from missed events
            await sync_snapshot()

            async with httpx.AsyncClient(timeout=None) as client:
                async with aconnect_sse(client, "GET", f"{CORE_URL}/replication/stream") as sse:
                    store.connected = True
                    store.last_connected_at = time.time()
                    store.reconnects += 1
                    backoff = 1
                    logger.info("[SSE] Connected (reconnect #%d)", store.reconnects)

                    ## start the event loop
                    async for event in sse.aiter_sse():
                        if not event.data:
                            continue

                        try:
                            ## parse the event
                            parsed = json.loads(event.data)
                        except json.JSONDecodeError as e:
                            store.errors["parse"] += 1
                            logger.error("[SSE] Parse error: %s", e)
                            continue

                        try:
                            ## apply the event to the store
                            await store.apply_event(parsed)
                        except Exception as e:
                            store.errors["apply"] += 1
                            logger.error("[SSE] Apply error: %s", e)

        except httpx.ConnectError:
            store.errors["connection"] += 1
            store.connected = False
            logger.error("[SSE] Cannot connect to Core")
        except Exception as e:
            store.errors["connection"] += 1
            store.connected = False
            logger.error("[SSE] Connection lost: %s", e)

        # Jitter prevents thundering herd when multiple replicas reconnect simultaneously
        jittered = backoff * (0.5 + random.random())
        logger.info("[SSE] Reconnecting in %.1fs...", jittered)
        await asyncio.sleep(jittered)
        backoff = min(backoff * 2, max_backoff)
