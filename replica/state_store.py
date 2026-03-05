"""
Replica state store: maintains local copy of Core's state.

Updated via SSE events from Core.
"""

import asyncio
import logging
import time

from .config import LIVE_RACE_STALE_SECONDS

logger = logging.getLogger(__name__)


class ReplicaStateStore:
    def __init__(self):
        ## track the last sequence id/live races
        self.last_sequence_id = 0
        self.live_races = {}
        ## track the horses, venues, scheduled races, and recent winners
        self.horses = {}
        self.venues = {}
        self.scheduled_races = []
        self.recent_winners = []
        ## track the events processed/reconnects/last event timestamp/started at/errors/connected/last connected at
        self.events_processed = 0
        self.reconnects = 0
        self.last_event_ts = 0.0
        self.started_at = time.time()

        self.errors = {
            "connection": 0,
            "parse": 0,
            "apply": 0,
        }
        self.connected = False
        self.last_connected_at = 0.0

        # Condition variable lets SSE clients wait for state changes efficiently
        self._condition = asyncio.Condition()

    async def apply_snapshot(self, snapshot: dict):
        """Load full state from Core snapshot."""
        now = time.time()
        ## load the snapshot
        self.last_sequence_id = snapshot.get("sequence_id", 0)
        self.horses = snapshot.get("horses", {})
        self.venues = snapshot.get("venues", {})
        self.scheduled_races = snapshot.get("scheduled_races", [])
        self.live_races = {}

        ## load the active races
        for race in snapshot.get("active_races", []):
            state = race["state"]
            race_id = race["race_id"]
            if "horses" in state:
                self.horses.update(state["horses"])
            state["last_update_ts"] = state.get("updated_at", now)
            self.live_races[race_id] = state

        logger.info(
            "[STORE] synced seq=%d horses=%d venues=%d races=%d",
            self.last_sequence_id,
            len(self.horses),
            len(self.venues),
            len(self.live_races),
        )

    async def apply_event(self, event: dict):
        """Apply incremental event from Core."""
        now = time.time()
        seq = event["sequence_id"]
        # Skip already-processed events (idempotency guard)
        if seq <= self.last_sequence_id:
            return

        self.last_sequence_id = seq
        self.events_processed += 1
        self.last_event_ts = now

        event_type = event["event_type"]
        payload = event.get("payload", {})
        if event_type == "RACE_STARTED":
            race_id = event["race_id"]
            venue_id = payload["venue_id"]
            horse_ids = payload["horse_ids"]

            self.live_races[race_id] = {
                "race_id": race_id,
                "status": "LIVE",
                "venue_id": venue_id,
                "distance_goal": payload["distance_goal"],
                "horse_ids": horse_ids,
                "positions": {},
                "odds": {},
                "last_update_ts": now,
            }
            # Race transitioned from scheduled to live
            self.scheduled_races = [r for r in self.scheduled_races if r.get("id") != race_id]

        elif event_type == "RACE_TICK":
            race_id = event["race_id"]
            # Skip ticks for unknown races (missed RACE_STARTED, will re-sync)
            if race_id not in self.live_races:
                return
            self.live_races[race_id]["status"] = payload.get("status", "LIVE")
            self.live_races[race_id]["positions"] = payload.get("positions", {})
            self.live_races[race_id]["odds"] = payload.get("odds", {})
            self.live_races[race_id]["last_update_ts"] = payload.get("updated_at", now)

        elif event_type == "RACE_FINISHED":
            race_id = event["race_id"]
            race = self.live_races.get(race_id, {})
            winner_id = payload.get("winner")
            venue_id = race.get("venue_id")
            venue = self.venues.get(venue_id, {})
            self.recent_winners.insert(0, {
                "race_id": race_id,
                "winner_id": winner_id,
                "winner_name": self.horses.get(winner_id, {}).get("name", "Unknown"),
                "venue_name": venue.get("name", "Unknown"),
                "finished_at": payload.get("finished_at"),
            })
            self.recent_winners = self.recent_winners[:10]
            self.live_races.pop(race_id, None)

        elif event_type == "RACE_SCHEDULED":
            self.scheduled_races.append({
                "id": payload.get("id"),
                "start_time": payload.get("start_time"),
                "status": payload.get("status"),
                "venue_id": payload.get("venue_id"),
                "horse_ids": payload.get("horse_ids", []),
            })

        self._prune_stale_races(now)

        async with self._condition:
            self._condition.notify_all()

    def _prune_stale_races(self, now: float):
        """Prune stale live races."""
        stale_ids = [
            race_id
            for race_id, race in self.live_races.items()
            if race.get("status") == "LIVE"
            and now - float(race.get("last_update_ts", 0.0)) > LIVE_RACE_STALE_SECONDS
        ]
        for race_id in stale_ids:
            self.live_races.pop(race_id, None)
        if stale_ids:
            logger.warning("[STORE] Pruned %d stale live race(s)", len(stale_ids))

    def get_lag_str(self, lag: float) -> str:
        """Get lag"""
        return f"{lag:.1f}s" if lag is not None else "N/A"

    def get_status_str(self, connected: bool) -> str:
        """Get current connection status."""
        return "connected" if connected else "disconnected"

    def get_errors_str(self, errors: dict) -> str:
        """Get the total number of errors."""
        return sum(errors.values())

    def metrics(self) -> dict:
        """Get the metrics for the replica."""
        now = time.time()
        uptime = now - self.started_at
        lag = now - self.last_event_ts if self.last_event_ts > 0 else None

        return {
            "sequence_id": self.last_sequence_id,
            "live_race_count": len(self.live_races),
            "events_processed": self.events_processed,
            "events_per_second": round(self.events_processed / max(uptime, 1), 2),
            "reconnects": self.reconnects,
            "uptime_seconds": round(uptime, 1),
            "connected": self.connected,
            "last_event_age_seconds": round(lag, 2) if lag else None,
            "errors": self.errors.copy(),
        }

## Singleton instance
store = ReplicaStateStore()
