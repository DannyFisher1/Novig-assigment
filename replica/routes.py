"""
Replica HTTP endpoints.

Serves read-only replicated state.
"""

import asyncio
import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from .state_store import store

router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "online", "sequence_id": store.last_sequence_id}

@router.get("/snapshot")
async def get_snapshot():
    """Full replicated state."""
    return {
        "sequence_id": store.last_sequence_id,
        "live_races": store.live_races,
        "scheduled_races": store.scheduled_races,
        "horses": store.horses,
        "venues": store.venues,
        "recent_winners": store.recent_winners,
    }


@router.get("/races/{race_id}")
async def get_race(race_id: str):
    """Single race by ID."""
    if race_id not in store.live_races:
        return {"error": "Race not found", "race_id": race_id}
    return store.live_races[race_id]


@router.get("/metrics")
async def metrics():
    return {"status": "replica", **store.metrics()}


@router.get("/stream")
async def stream(request: Request):
    """SSE stream of state updates."""

    async def generate():
        last_seq = store.last_sequence_id
        while True:
            if await request.is_disconnected():
                break

            async with store._condition:
                try:
                    await asyncio.wait_for(
                        store._condition.wait_for(lambda: store.last_sequence_id > last_seq),
                        timeout=15.0,
                    )
                except TimeoutError:
                    # SSE comment keeps connection alive through proxies
                    yield ": keepalive\n\n"
                    continue

            data = {
                "sequence_id": store.last_sequence_id,
                "live_races": store.live_races,
                "scheduled_races": store.scheduled_races,
                "recent_winners": store.recent_winners,
            }
            ## manually send the event
            yield f"id: {store.last_sequence_id}\nevent: update\ndata: {json.dumps(data)}\n\n"
            last_seq = store.last_sequence_id

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/stream/{race_id}")
async def stream_race(race_id: str, request: Request):
    """SSE stream for a single race. Closes when race finishes."""

    async def generate():
        last_seq = store.last_sequence_id

        # Check if race exists
        if race_id not in store.live_races:
            yield f"event: error\ndata: {json.dumps({'error': 'Race not found', 'race_id': race_id})}\n\n"
            return

        while True:
            if await request.is_disconnected():
                break

            # Race finished - send final state and close
            if race_id not in store.live_races:
                yield f"event: finished\ndata: {json.dumps({'race_id': race_id, 'status': 'finished'})}\n\n"
                break

            async with store._condition:
                try:
                    await asyncio.wait_for(
                        store._condition.wait_for(lambda: store.last_sequence_id > last_seq),
                        timeout=5.0,
                    )
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue

            # Send race update if still live
            if race_id in store.live_races:
                race = store.live_races[race_id]
                data = {
                    "sequence_id": store.last_sequence_id,
                    "race_id": race_id,
                    **race,
                }
                yield f"id: {store.last_sequence_id}\nevent: tick\ndata: {json.dumps(data)}\n\n"

            last_seq = store.last_sequence_id

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"}
    )
