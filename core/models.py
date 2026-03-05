"""Domain models for the horse racing simulation."""

from enum import Enum
from typing import List
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Surface(str, Enum):
    DIRT = "Dirt"
    TURF = "Turf"
    MUD = "Mud"


class Weather(str, Enum):
    SUNNY = "Sunny"
    RAINY = "Rainy"


class RaceStatus(str, Enum):
    PRE_RACE = "PRE_RACE"
    LIVE = "LIVE"
    FINISHED = "FINISHED"


class Horse(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    speed: float
    traction: float

    @staticmethod
    def ids_as_strings(horses: List["Horse"]) -> List[str]:
        return [str(h.id) for h in horses]


class Venue(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    surface: Surface
    weather: Weather
    distance: int
