"""
Core HTTP endpoints.

- Health/metrics for observability
- Replication endpoints (snapshot + SSE stream)
"""

import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
async def health():
    return {"status": "running"}


@router.get("/metrics")
async def metrics(request: Request):
    manager = request.app.state.manager
    return {
        "captured_at": time.time(),
        "core": {
            "active_races": len(manager.active_engines),
            "scheduled_pending": manager.scheduler.get_pending_count(),
            "sequence_id": manager.sequence_id,
        },
    }


@router.get("/replication/snapshot")
async def replication_snapshot(request: Request):
    """Full state snapshot for replica sync."""
    return request.app.state.manager.get_snapshot()


@router.get("/replication/stream")
async def replication_stream(request: Request):
    """SSE stream of events for replicas."""
    manager = request.app.state.manager
    shutdown_event = request.app.state.shutdown_event

    async def generate():
        manager.sse_connections += 1
        logger.info("[SSE] Replica connected (total: %d)", manager.sse_connections)

        try:
            last_seq = manager.sequence_id

            while not shutdown_event.is_set():
                if await request.is_disconnected():
                    break

                event = await manager.wait_for_event(last_seq, timeout=2.0)

                if event is None:
                    # SSE comment keeps connection alive through proxies
                    yield ": keepalive\n\n"
                else:
                    yield f"id: {event['sequence_id']}\nevent: {event['event_type']}\ndata: {json.dumps(event)}\n\n"
                    last_seq = event["sequence_id"]
        finally:
            manager.sse_connections -= 1
            logger.info("[SSE] Replica disconnected (total: %d)", manager.sse_connections)


    ## return the streaming response
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"}
    )
