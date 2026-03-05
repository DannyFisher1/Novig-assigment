"""Generate randomized horses and venues for the simulation."""

import random
import uuid
from typing import List, Tuple

from coolname import generate_slug

from .config import SEED_HORSES, SEED_VENUES
from .models import Horse, Surface, Venue, Weather


def generate_game_data(
    num_horses: int = SEED_HORSES,
    num_venues: int = SEED_VENUES,
) -> Tuple[List[Horse], List[Venue]]:
    """Create a randomized set of horses and race venues."""
    horses = [
        Horse(
            id=uuid.uuid4(),
            name=generate_slug(random.randint(2, 4)).replace("-", " ").title(),
            speed=round(random.gauss(87, 2)),
            traction=round(random.uniform(0.5, 1), 2),
        )
        for _ in range(num_horses)
    ]

    venues = [
        Venue(
            id=uuid.uuid4(),
            name=generate_slug(2).replace("-", " ").title() + " Speedway",
            surface=random.choice(list(Surface)),
            weather=random.choice(list(Weather)),
            distance=random.randint(1000, 5000), #1km - 5km
        )
        for _ in range(num_venues)
    ]

    return horses, venues
