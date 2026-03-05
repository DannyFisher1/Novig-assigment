"""Race scheduling: maintains a queue of upcoming races."""

import logging
import random
import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import List

from pydantic import BaseModel, Field

from ..config import RACE_PARTICIPANTS
from ..models import Horse, RaceStatus, Venue

logger = logging.getLogger(__name__)


class ScheduledRace(BaseModel):
    id: uuid.UUID
    horses: List[Horse]
    venue: Venue
    start_time: datetime
    status: str = Field(default_factory=lambda: RaceStatus.PRE_RACE.value)


class SchedulePlanner:
    """Manages the race schedule queue with rotating horse pool."""

    def __init__(self, horses: List[Horse], venues: List[Venue], interval_seconds: int):
        self.horses = horses
        self.venues = venues
        self.interval_seconds = interval_seconds
        # Rotating pool ensures all horses race with roughly equal frequency
        self.horse_pool: deque[Horse] = deque(horses)
        self.schedule: List[ScheduledRace] = []

    def create_initial_schedule(self, count: int):
        """Build initial batch of scheduled races."""
        now = datetime.now()
        for i in range(count):
            start_time = now + timedelta(seconds=i * self.interval_seconds)
            race = self._build_race(start_time)
            self.schedule.append(race)
            logger.info("[SCHEDULER] %s at %s", race.venue.name, race.start_time.strftime("%H:%M:%S"))

    def schedule_next_race(self) -> ScheduledRace:
        """Add a new race to the end of the schedule. Returns the new race."""
        last_time = self.schedule[-1].start_time if self.schedule else datetime.now()
        start_time = last_time + timedelta(seconds=self.interval_seconds)
        race = self._build_race(start_time)
        self.schedule.append(race)
        logger.info("[SCHEDULER] Scheduled %s at %s", race.venue.name, race.start_time.strftime("%H:%M:%S"))
        return race


    def remove_race(self, race: ScheduledRace):
        if race in self.schedule:
            self.schedule.remove(race)

    def get_due_races(self, now: datetime) -> List[ScheduledRace]:
        return [r for r in self.schedule if r.status == RaceStatus.PRE_RACE.value and now >= r.start_time]

    def get_pending_count(self) -> int:
        return len([r for r in self.schedule if r.status == RaceStatus.PRE_RACE.value])

    def _build_race(self, start_time: datetime) -> ScheduledRace:
        """Create race with rotated horses and random venue."""
        # Pop from front, push to back - cycles through all horses fairly
        participants: List[Horse] = []
        for _ in range(min(RACE_PARTICIPANTS, len(self.horse_pool))):
            horse = self.horse_pool.popleft()
            participants.append(horse)
            self.horse_pool.append(horse)

        return ScheduledRace(
            id=uuid.uuid4(),
            horses=participants,
            venue=random.choice(self.venues),
            start_time=start_time,
        )
