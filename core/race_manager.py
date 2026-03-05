"""
Race Manager - orchestrates the simulation.

Responsibilities:
- Schedules races at regular intervals
- Starts race engines when races are due
- Publishes events (RACE_STARTED, RACE_TICK, RACE_FINISHED, RACE_SCHEDULED)
- Provides snapshot for replica sync
"""

import asyncio
import logging
import time
import uuid
from collections import deque
from datetime import datetime
from typing import Any, Dict, List

from .config import (
    EVENT_BUFFER_SIZE,
    INITIAL_SCHEDULE_COUNT,
    MANAGER_HEARTBEAT_LOG_SECONDS,
    MIN_SCHEDULED_RACES,
    RACE_START_INTERVAL_SECONDS,
)
from .logic import SchedulePlanner
from .models import Horse, RaceStatus, Venue
from .race_engine import RaceEngine, RaceResult

logger = logging.getLogger(__name__)


class RaceManager:
    def __init__(self, horses: List[Horse], venues: List[Venue]):
        ## create the scheduler
        self.scheduler = SchedulePlanner(horses, venues, interval_seconds=RACE_START_INTERVAL_SECONDS)

        ## track the active engines/tasks
        self.active_engines: Dict[uuid.UUID, RaceEngine] = {}
        self.active_tasks: Dict[uuid.UUID, asyncio.Task] = {}

        # Event buffer and condition variable for SSE streaming
        self.sequence_id = 0
        self._condition = asyncio.Condition()
        self._events: deque[dict[str, Any]] = deque(maxlen=EVENT_BUFFER_SIZE)

        # Track active SSE subscribers for monitoring
        self.sse_connections = 0

        logger.info("[MANAGER] Initialized with %d horses, %d venues", len(horses), len(venues))
        self.scheduler.create_initial_schedule(count=INITIAL_SCHEDULE_COUNT)

    async def publish(self, event_type: str, race_id: uuid.UUID | None, payload: dict[str, Any]):
        """Publish event and notify SSE subscribers."""
        self.sequence_id += 1
        event = {
            "sequence_id": self.sequence_id,
            "event_type": event_type,
            "race_id": str(race_id) if race_id else None,
            "timestamp": time.time(),
            "payload": payload,
        }
        self._events.append(event)
        ## notify all the waiting tasks that the event is available
        async with self._condition:
            self._condition.notify_all()

    async def wait_for_event(self, last_seq: int, timeout: float = 15.0) -> dict | None:
        """Wait for next event, or return None on timeout (for keepalive)."""
        event = self._next_event_after(last_seq)
        if event is not None:
            return event

        try:
            async with self._condition:
                ## wait for the next event to be available
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self.sequence_id > last_seq),
                    timeout=timeout,
                )
        except TimeoutError:
            return None

        return self._next_event_after(last_seq)

    def _next_event_after(self, last_seq: int) -> dict[str, Any] | None:
        for event in self._events:
            ## return the next event after the last_seq
            if event["sequence_id"] > last_seq:
                return event
        return None

    def get_snapshot(self) -> dict[str, Any]:
        """Full state snapshot for replica sync."""
        return {
            "sequence_id": self.sequence_id,
            "active_races": [
                {"race_id": str(race_id), "state": engine.snapshot()}
                for race_id, engine in self.active_engines.items()
            ],
            "scheduled_races": [
                {
                    "id": str(r.id),
                    "start_time": r.start_time.isoformat(),
                    "status": r.status,
                    "venue_id": str(r.venue.id),
                    "horse_ids": Horse.ids_as_strings(r.horses),
                }
                for r in self.scheduler.schedule
                if r.status == RaceStatus.PRE_RACE.value
            ],
            "horses": {
                str(h.id): {"name": h.name, "speed": h.speed, "traction": h.traction}
                for h in self.scheduler.horses
            },
            "venues": {
                str(v.id): {"name": v.name, "surface": v.surface.value, "weather": v.weather.value, "distance": v.distance}
                for v in self.scheduler.venues
            },
            "captured_at": time.time(),
        }

    async def main_loop(self):
        """Main loop: start due races and log periodic heartbeats."""
        logger.info("[MANAGER] Main loop started")
        last_heartbeat = time.time()

        while True:
            now = datetime.now()
            ## get the due races and start them
            for race in self.scheduler.get_due_races(now):
                if race.id in self.active_engines:
                    continue
                await self._start_race(race)

            if time.time() - last_heartbeat >= MANAGER_HEARTBEAT_LOG_SECONDS:
                logger.info(
                    "[MANAGER] active=%d pending=%d seq=%d replicas=%d",
                    len(self.active_engines),
                    self.scheduler.get_pending_count(),
                    self.sequence_id,
                    self.sse_connections,
                )
                last_heartbeat = time.time()

            await asyncio.sleep(1)

    async def _start_race(self, race):
        """Initialize engine and publish RACE_STARTED event."""
        race.status = RaceStatus.LIVE.value

        ## create the race engine and add it to the active engines
        engine = RaceEngine(
            race_id=race.id,
            horses=race.horses,
            venue=race.venue,
            publish_event=self.publish,
        )
        self.active_engines[race.id] = engine

        ## publish the RACE_STARTED event
        payload = engine.started_payload()
        payload["start_time"] = race.start_time.isoformat()
        await self.publish("RACE_STARTED", race.id, payload)

        ## create the task to run the race
        self.active_tasks[race.id] = asyncio.create_task(self._run_race(engine, race))

    async def _run_race(self, engine: RaceEngine, race):
        """Run race to completion and publish RACE_FINISHED event."""
        try:
            ## run the race to completion and publish the RACE_FINISHED event
            result: RaceResult = await engine.run_simulation()
            await self.publish("RACE_FINISHED", race.id, engine.finished_payload(result.winner_id))
        except Exception as e:
            logger.exception("[MANAGER] Race %s error: %s", race.id, e)
        finally:
            self.active_tasks.pop(race.id, None)
            self.active_engines.pop(race.id, None)
            self.scheduler.remove_race(race)
            while self.scheduler.get_pending_count() < MIN_SCHEDULED_RACES:
                ## schedule/publish the next race if the pending count is less than the minimum scheduled races
                new_race = self.scheduler.schedule_next_race()
                await self.publish("RACE_SCHEDULED", new_race.id, {
                    "id": str(new_race.id),
                    "start_time": new_race.start_time.isoformat(),
                    "status": new_race.status,
                    "venue_id": str(new_race.venue.id),
                    "horse_ids": Horse.ids_as_strings(new_race.horses),
                })
