"""Race simulation engine - handles a single race from start to finish."""

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List

from .config import RACE_TICK_SECONDS
from .logic import calculate_odds, compute_move
from .models import Horse, RaceStatus, Venue

logger = logging.getLogger(__name__)


@dataclass
class RaceResult:
    winner_id: str | None
    total_ticks: int
    finished_at: float


class RaceEngine:
    """Simulates a single race, publishing tick events as it progresses."""

    def __init__(
        self,
        race_id: uuid.UUID,
        horses: List[Horse],
        venue: Venue,
        publish_event: Callable[[str, uuid.UUID, dict], Awaitable[None]],
        positions: Dict[str, float] | None = None,
        sequence_id: int = 0,
        status: RaceStatus = RaceStatus.PRE_RACE,
        winners: List[str] | None = None,
    ):
        self.race_id = race_id
        self.horses = horses
        self.venue = venue
        self.publish_event = publish_event
        self.distance_goal = venue.distance
        self.positions: Dict[str, float] = positions or {str(h.id): 0.0 for h in horses}
        self.status = status
        self.sequence_id = sequence_id
        self.winners: List[str] = winners or []


    async def run_simulation(self) -> RaceResult:
        logger.info("[RACE_ENGINE] Race %s started at %s", self.race_id, self.venue.name)
        self.status = RaceStatus.LIVE

        # Cache frequently accessed values to avoid repeated attribute lookups in hot loop
        distance_goal = self.distance_goal
        positions = self.positions
        publish_tick = self._publish_tick
        tick_sleep = asyncio.sleep
        tick_seconds = RACE_TICK_SECONDS

        while self.status == RaceStatus.LIVE:
            self.sequence_id += 1

            # Store tuples of (fraction_of_tick_to_finish, horse_id)
            finishers: list[tuple[float, str]] = []

            for horse in self.horses:
                h_id = str(horse.id)
                move = compute_move(horse, self.venue)
                current_pos = positions[h_id]
                new_pos = current_pos + move

                if new_pos >= distance_goal:
                    # Calculate exactly when in this tick the horse crossed the line (0.0 to 1.0)
                    fraction_of_tick = (distance_goal - current_pos) / move
                    positions[h_id] = distance_goal
                    finishers.append((fraction_of_tick, h_id))
                else:
                    positions[h_id] = new_pos

            if finishers:
                # Shuffle first to randomize tie-breakers for horses crossing at same fraction
                random.shuffle(finishers)

                # Sort by fraction to find who crossed the finish line first within this tick
                finishers.sort(key=lambda x: x[0])

                # 3. Only the first one matters
                true_winner_id = finishers[0][1]
                self.winners.append(true_winner_id)

                self.winner_name = next(h.name for h in self.horses if str(h.id) == true_winner_id)

                # End the race
                self.status = RaceStatus.FINISHED

            # Publish the state of the race for this tick
            await publish_tick()
            await tick_sleep(tick_seconds)

        logger.info("[RACE_ENGINE] Race %s finished. Winner: %s", self.race_id, self.winner_name)
        return RaceResult(
            winner_id=true_winner_id,
            total_ticks=self.sequence_id,
            finished_at=time.time(),
        )

    async def _publish_tick(self):
        await self.publish_event("RACE_TICK", self.race_id, self.tick_payload())

    def _calculate_odds(self) -> Dict[str, float]:
        return calculate_odds(self.horses, self.positions, self.venue, self.distance_goal)

    def started_payload(self) -> dict:
        """Lean payload for RACE_STARTED event."""
        return {
            "venue_id": str(self.venue.id),
            "horse_ids": [str(h.id) for h in self.horses],
            "distance_goal": self.distance_goal,
        }

    def tick_payload(self) -> dict:
        """Minimal payload for RACE_TICK event."""
        return {
            "positions": self.positions,
            "odds": self._calculate_odds(),
            "status": self.status.value,
            "updated_at": time.time(),
        }

    def finished_payload(self, winner_id: str) -> dict:
        """Lean payload for RACE_FINISHED event."""
        return {
            "winner": winner_id,
            "finished_at": time.time(),
        }

    def snapshot(self) -> dict:
        """Return current state for replication snapshot."""
        return {
            "race_id": str(self.race_id),
            "status": self.status.value if isinstance(self.status, RaceStatus) else self.status,
            "sequence_id": self.sequence_id,
            "venue_id": str(self.venue.id),
            "venue_name": self.venue.name,
            "venue_surface": self.venue.surface.value,
            "venue_weather": self.venue.weather.value,
            "distance_goal": self.distance_goal,
            "horse_ids": Horse.ids_as_strings(self.horses),
            "horses": {str(h.id): {"name": h.name, "speed": h.speed, "traction": h.traction} for h in self.horses},
            "positions": self.positions,
            "odds": self._calculate_odds(),
            "winners": self.winners,
            "updated_at": time.time(),
        }
