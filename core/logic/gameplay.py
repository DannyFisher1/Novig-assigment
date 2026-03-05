"""
Gameplay mechanics: horse movement and odds calculation.

Odds use a prediction market model:
- Prices represent win probability (0-100%)
- Early race: high variance, prices relatively even
- Late race: low variance, leader approaches 100%
- At finish: winner = 100%, losers = 0%
"""

import math
import random
from typing import Dict, List

from ..models import Horse, Surface, Venue, Weather

# Weather/surface modifiers
from ..config import (
    RAIN_BASE_SPEED_FACTOR,
    RAIN_TRACTION_BONUS,
    MUD_BASE_SPEED_FACTOR,
    MUD_TRACTION_BONUS,
    MOVE_VARIANCE_STDDEV,
    BURST_CHANCE,
    BURST_MULTIPLIER,
    MIN_MOVE,
)


def effective_speed(horse: Horse, venue: Venue) -> float:
    """Calculate speed adjusted for conditions (traction matters in bad weather)."""
    speed = horse.speed

    if venue.weather == Weather.RAINY:
        # Traction matters in rain: high traction = less slowdown
        speed *= RAIN_BASE_SPEED_FACTOR + RAIN_TRACTION_BONUS * horse.traction

    if venue.surface == Surface.MUD:
        # Mud slows everyone, but traction helps
        speed *= MUD_BASE_SPEED_FACTOR + MUD_TRACTION_BONUS * horse.traction

    return speed


def compute_move(horse: Horse, venue: Venue) -> float:
    """Calculate distance a horse moves this tick."""
    speed = effective_speed(horse, venue)

    # Additional per-tick randomness for rain
    if venue.weather == Weather.RAINY:
        speed_variance = random.randint(-10, 0)
        speed = max(1, speed + speed_variance)

    # Base movement with gaussian noise for natural variation
    move = (speed / 10.0) * random.gauss(1, MOVE_VARIANCE_STDDEV)

    # Occasional burst of speed adds excitement and unpredictability
    if random.random() < BURST_CHANCE:
        move *= BURST_MULTIPLIER

    return max(MIN_MOVE, move)


def calculate_odds(
    horses: List[Horse],
    positions: Dict[str, float],
    venue: Venue,
    distance_goal: int,
) -> Dict[str, float]:
    """
    Prediction market style odds.

    Model:
    1. Calculate expected ticks to finish (using effective speed)
    2. Variance decreases as race progresses (less time for upsets)
    3. Convert time gaps to probabilities using softmax
    4. Leader approaches 100% as race nears end

    Returns {horse_id: probability} summing to 1.0.
    """
    if not horses or not positions:
        return {str(h.id): 1.0 / len(horses) for h in horses}

    # Expected ticks to finish for each horse
    finish_ticks = {}
    for horse in horses:
        horse_id = str(horse.id)
        pos = positions.get(horse_id, 0)
        remaining = max(distance_goal - pos, 1)
        eff_speed = effective_speed(horse, venue)
        ticks = remaining / (eff_speed / 10.0)
        finish_ticks[horse_id] = ticks

    # Leader = lowest finish time
    leader_ticks = min(finish_ticks.values())

    # Race progress and remaining variance
    leader_pos = max(positions.values())
    progress = leader_pos / distance_goal if distance_goal > 0 else 0

    # Variance = how much randomness remains
    # Early race (progress=0): high variance, upsets possible
    # Late race (progress=1): low variance, leader wins
    # sqrt models decreasing uncertainty
    remaining_pct = 1 - progress
    variance = max(remaining_pct ** 0.5 * 50, 0.5)  # 50 early -> 0.5 late

    # Calculate probability scores
    # Gap = how many ticks behind the leader
    # Score = exp(-gap / variance) — exponential decay based on gap
    scores = {}
    for horse_id, ticks in finish_ticks.items():
        gap = ticks - leader_ticks  # 0 for leader, positive for others
        scores[horse_id] = math.exp(-gap / variance)

    # Normalize to probabilities
    total = sum(scores.values())
    if total <= 0:
        return {str(h.id): 1.0 / len(horses) for h in horses}

    return {h_id: round(s / total, 4) for h_id, s in scores.items()}
