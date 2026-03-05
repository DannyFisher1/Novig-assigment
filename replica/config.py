"""Replica service configuration."""

import os

CORE_URL = os.getenv("CORE_URL", "http://localhost:8000")
LIVE_RACE_STALE_SECONDS = 120

HEARTBEAT_INTERVAL = 15
