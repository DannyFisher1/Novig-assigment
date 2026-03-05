# Horse Racing Simulation

Novig Backend Engineer take-home submittion. 

## Task

Build two services:

- a core that owns and updates some in-memory state, and
- a replica that keeps up with it and serves reads.

## How to Run

```bash
# Install dependencies
uv sync

# Terminal 1: Start Core 
uv run uvicorn core.app:app --port 8000 --timeout-graceful-shutdown 2

# Terminal 2: Start Replica
uv run uvicorn replica.app:app --port 8001

# Terminal 3: Run demo
uv run python demo/replica_demo.py
```

**Or with Docker Compose:**

```bash
docker compose up --build
```

**Verify it's working:**

```bash
curl localhost:8000/health  # {"status":"running"}
curl localhost:8001/health  # {"status":"online","sequence_id":...}
curl localhost:8001/metrics # Full replica metrics with connection state
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              CORE (:8000)                                │
│                                                                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────────┐   │
│  │  Scheduler  │───►│ RaceEngine  │───►│  SSE Stream                 │   │
│  │  (queues    │    │  (50ms tick │    │  /replication/stream        │   │
│  │   races)    │    │   loop)     │    │                             │   │
│  └─────────────┘    └─────────────┘    └──────────────┬──────────────┘   │
│                                                       │                  │
│  State: horses[], venues[], active_races{}            │ RACE_STARTED     │
│         sequence_id (monotonic)                       │ RACE_TICK        │
│                                                       │ RACE_FINISHED    │
└───────────────────────────────────────────────────────┼──────────────────┘
                                                        │
                                                        ▼ SSE (Server-Sent Events)
┌───────────────────────────────────────────────────────────────────────────┐
│                            REPLICA (:8001)                                │
│                                                                           │
│  ┌──────────────────┐    ┌─────────────────────────────────────────────┐  │
│  │  SSE Subscriber  │───►│  StateStore                                 │  │
│  │  (httpx-sse)     │    │  - horses, venues (from sync)               │  │
│  │                  │    │  - live_races, scheduled, recent_winners    │  │
│  │  On disconnect:  │    │  - sequence_id, lag, errors (observability) │  │
│  │  re-sync         │    └─────────────────────────────────────────────┘  │
│  │  + exponential   │                         │                           │
│  │  backoff         │                         │ /stream, /stream/{race_id}│
│  └──────────────────┘                         ▼                           │
│                                        ┌─────────────┐                    │
│  Heartbeat: logs status every 15s      │  Clients    │                    │
│  [HEARTBEAT] connected seq=X lag=0.1s  │  (Dashboard)│                    │
│                                        └─────────────┘                    │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## State Machine: What & Why

### Domain Model


| Entity    | Attributes                                                                   | Why                                                              |
| --------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| **Horse** | `id`, `name`, `speed` (85-90), `traction` (0.5-1.0)                          | Speed is base velocity; traction affects performance in rain/mud |
| **Venue** | `id`, `name`, `surface` (Dirt/Turf/Mud), `weather` (Sunny/Rainy), `distance` | Conditions affect race dynamics                                  |
| **Race**  | `id`, `status`, `positions{}`, `odds{}`, `horse_ids[]`                       | Tracks live state per race                                       |


### Race Lifecycle

```
SCHEDULED ──► LIVE ──► FINISHED
                │
                └── RACE_TICK every 50ms
                    - Update positions (speed ± randomness)
                    - Recalculate odds 
                    - Broadcast to replicas
```

### Why this design?

1. **Perpetual simulation** - The system runs continuously without manual intervention. Breaking the lifecycle into discrete states (`SCHEDULED → LIVE → FINISHED`) lets the `race_manager` automatically queue new races if the number of races is below the set threshold.
2. **Conditions create variance** - Each horse has `speed` (base velocity) and `traction` (grip). Venues have `surface` (dirt/turf/mud) and `weather` (sunny/rainy). Rain + mud penalizes low-traction horses. I needed the venue factors so that the top speed/traction horse wouldn't win every time.
3. **Market odds** (% change to win)- Win probabilities are calculated each tick using expected time-to-finish. Early in a race, variance is high (upsets possible). As the leader approaches the finish, variance decays and their probability trends toward 100%. This mirrors how real betting markets behave

---

## Replication: Data Flow, Format, Consistency

### Data Flow

```
1. SYNC (on connect/reconnect)
   Replica ──GET /replication/snapshot──► Core
   Replica ◄── {sequence_id, horses, venues, active_races} ──

2. STREAM (continuous)
   Replica ◄── SSE: RACE_STARTED {venue_id, horse_ids, distance} ──
   Replica ◄── SSE: RACE_TICK {positions, odds, updated_at} ──
   Replica ◄── SSE: RACE_FINISHED {winner, finished_at} ──

3. RECONNECT (on disconnect)
   - Exponential backoff: 1s → 2s → 4s → ... → 30s max
   - Re-sync from snapshot 
```

### Event Format

```json
{
  "sequence_id": 12345,
  "event_type": "RACE_TICK",
  "race_id": "abc-123",
  "payload": {
    "positions": {"horse-1": 5432.1, "horse-2": 5401.8},
    "odds": {"horse-1": 0.65, "horse-2": 0.25},
    "status": "LIVE",
    "updated_at": 1709567890.123
  }
}
```

### Consistency Model

Eventually consistent. Replicas typically lag by ~50ms (one tick). Each event has a `sequence_id` so replicas can detect gaps and ignore duplicates. Since replicas are read-only, there's no conflict resolution to worry about. If a replica disconnects, it just grabs a fresh snapshot when it reconnects.

---

## Key Trade-offs


| Decision                     | Why                                                                                           | Downside                                                                |
| ---------------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **SSE over WebSocket/Redis** | Works over plain HTTP, no external deps, can debug with curl. I only need server→client push. | Can't scale horizontally without sticky sessions or adding Redis later. |
| **Re-sync on disconnect**    | Simpler than tracking gaps. Snapshot is tiny (~KB), so just grab a fresh one.                 | Slightly heavier than delta sync, but fine for this use case.           |
| **No event buffer on Core**  | Less code, less memory. If a replica falls behind, it re-syncs anyway.                        | Can't replay old events. Not a problem here.                            |
| **50ms tick rate**           | Feels smooth in the UI (~20 updates/sec). Fast enough to be interesting.                      | Generates a lot of events. Could batch if needed.                       |
| **In-memory only**           | Fast, no DB setup, keeps the project simple.                                                  | State disappears on restart. Would add sqlite for prod.                 |
| **httpx-sse**                | Handles SSE parsing cleanly. Didn't want to parse `data:` lines manually.                     | Another dependency, but worth it.                                       |


---

## API Reference


| Core (:8000)                | Replica (:8001)                                         |
| --------------------------- | ------------------------------------------------------- |
| `GET /health`               | `GET /health`                                           |
| `GET /metrics`              | `GET /metrics` (includes connection state, lag, errors) |
| `GET /replication/snapshot` | `GET /snapshot`                                         |
| `GET /replication/stream`   | `GET /stream`                                           |
|                             | `GET /stream/{race_id}` (closes on race finish)         |
|                             | `GET /races/{id}`                                       |


---

## Observability

```bash
# Core logs (every 15s)
[MANAGER] active=2 pending=11 seq=12345 replicas=1

# Replica heartbeat (every 15s)
[HEARTBEAT] connected seq=12345 races=2 lag=0.1s errors=0

# Replica metrics endpoint
curl localhost:8001/metrics
{
  "status": "replica",
  "connected": true,
  "sequence_id": 12345,
  "last_event_age_seconds": 0.03,
  "errors": {"connection": 0, "parse": 0, "apply": 0},
  "events_processed": 50000,
  "reconnects": 1
}
```

---

## Project Structure

```
core/                      # Authoritative state machine
  app.py                   # FastAPI + lifespan + graceful shutdown
  config.py                # Simulation constants
  models.py                # Horse, Venue, RaceStatus models
  seeding.py               # Random world generation
  race_manager.py          # Orchestrates races + SSE broadcast
  race_engine.py           # Single race simulation loop
  routes.py                # HTTP endpoints
  logic/
    gameplay.py            # Movement + odds calculation
    scheduling.py          # Race queue management

replica/                   # Read-only follower
  app.py                   # FastAPI + heartbeat loop
  config.py                # CORE_URL and settings
  subscriber.py            # SSE client + reconnect logic
  state_store.py           # Local state + metrics
  routes.py                # HTTP + SSE endpoints

demo/
  replica_demo.py          # Terminal UI demo
```

---

## AI & Tools

**Tools Used:**

- Claude (Anthropic) via Claude Code CLI
- Codex (Openai) via Codex CLI

**Docs** 

- [FastAPI](https://fastapi.tiangolo.com/)
- [httpx](https://github.com/florimondmanca/httpx-sse?tab=readme-ov-file#installation)
- [httpx-sse](https://github.com/florimondmanca/httpx-sse?tab=readme-ov-file#installation)

**What it helped with:**

- httpx-sse integration for cleaner code
- Market odds formula (variance decay model) 
- Debugging: helping find various issues across the whole codebase
- Demo page is mostly AI generated with me ensuring we are actually just hitting the correct endpoints and connecting t othe sse

**Where I disagreed/steered differently:**

- **Redis pub/sub**: Claude initially suggested Redis for replication fan-out. I disagreed however as SSE is simpler, has zero external dependencies, and is sufficient for this scale. Redis would be right for 100+ replicas, not 1-3. 
- **Gap healing buffer**: Suggested implementing a ring buffer for gap healing on reconnect. I chose re-sync instead.  This made the code simpler and the snapshot smaller. Also I do not need to worry about a buffer overflow.
- **Odds calculation complexity**: Early implementations of the scoring formula got way too complex and so I had to take a step back and reapproach the formulas with multiple factors (position weight, traction severity, sharpness exponents). I simplified to `score = effective_speed / remaining_distance` with variance decay. Easier to reason about, fewer bugs.
- **Over-engineering**: Had to trim features like per-race SSE streams on Core (unnecessary when Replica can filter), error recovery logic (simple reconnect works), and extra logging. Working with LLMs is extremely prone to scope creep. I find that they will happily add features you didn't ask for all for the sake of fixing a certain bug. The most important skill is keeping the model focused on the initial requirements and saying "no" to suggestions that add complexity without clear value.

## Dependencies

```
fastapi, uvicorn, httpx, httpx-sse, pydantic, rich, coolname
```

# Novig-assigment
