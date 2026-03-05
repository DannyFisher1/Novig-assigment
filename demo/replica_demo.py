#!/usr/bin/env python3
"""
Replica Demo: Demonstrates the replica's full functionality.

Shows:
- Metrics with connection state, replication lag, and error tracking
- Full state snapshot (horses, venues, races)
- Live SSE streaming with detailed race information
- Real-time position updates, odds, and race outcomes

Usage:
    uv run python demo/replica_demo.py
"""

import asyncio
import json
import sys
import time

import httpx
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.text import Text
from rich import box

REPLICA_URL = "http://localhost:8001"
console = Console()


def format_probability(probability: float) -> tuple[str, str]:
    """Format probability as percentage with color coding.

    Returns (text, style) for prediction market display.
    """
    if probability <= 0 or probability >= 1:
        return "N/A", "dim"

    pct = probability * 100

    # Color based on probability
    if pct >= 50:
        style = "green bold"
    elif pct >= 25:
        style = "yellow"
    elif pct >= 10:
        style = "white"
    else:
        style = "dim"

    return f"{pct:.0f}%", style


def make_metrics_panel(metrics: dict) -> Panel:
    """Create a panel showing replica metrics."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    connected = metrics.get("connected", False)
    conn_style = "green bold" if connected else "red bold"
    table.add_row("Connected", Text(str(connected), style=conn_style))

    lag = metrics.get("last_event_age_seconds")
    if lag is not None:
        lag_style = "green" if lag < 0.5 else "yellow" if lag < 2 else "red"
        table.add_row("Replication Lag", Text(f"{lag:.2f}s", style=lag_style))
    else:
        table.add_row("Replication Lag", Text("N/A", style="dim"))

    table.add_row("Sequence ID", str(metrics.get("sequence_id", 0)))
    table.add_row("Events Processed", str(metrics.get("events_processed", 0)))
    table.add_row("Events/sec", str(metrics.get("events_per_second", 0)))
    table.add_row("Reconnects", str(metrics.get("reconnects", 0)))
    table.add_row("Uptime", f"{metrics.get('uptime_seconds', 0):.1f}s")

    errors = metrics.get("errors", {})
    error_total = sum(errors.values())
    if error_total > 0:
        table.add_row("Errors", Text(str(errors), style="red"))
    else:
        table.add_row("Errors", Text("None", style="green"))

    return Panel(table, title="Replica Metrics", border_style="cyan")


def make_race_panel(race: dict, horses: dict, venues: dict, show_stats: bool = True) -> Panel:
    """Create a detailed panel for a single race."""
    race_id = race.get("race_id", "?")[:12]
    status = race.get("status", "UNKNOWN")
    venue_id = race.get("venue_id", "")
    venue = venues.get(venue_id, {})
    distance_goal = race.get("distance_goal", 0)

    # Header with venue info
    venue_name = venue.get("name", "Unknown Venue")
    surface = venue.get("surface", "?")
    weather = venue.get("weather", "?")
    header = f"{venue_name} | {surface} | {weather} | {distance_goal}m"

    # Build horse table
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("Pos", style="dim", width=4)
    table.add_column("Horse", style="cyan", min_width=18)
    if show_stats:
        table.add_column("Spd", justify="right", width=4, style="yellow")
        table.add_column("Trc", justify="right", width=4, style="magenta")
    table.add_column("Distance", justify="right", width=8)
    table.add_column("Progress", width=16)
    table.add_column("Win %", justify="right", width=6)

    positions = race.get("positions", {})
    odds = race.get("odds", {})
    horse_ids = race.get("horse_ids", list(positions.keys()))

    # Sort by position (descending)
    sorted_horses = sorted(
        horse_ids,
        key=lambda h: positions.get(h, 0),
        reverse=True
    )

    for rank, horse_id in enumerate(sorted_horses, 1):
        horse = horses.get(horse_id, {})
        name = horse.get("name", horse_id[:15])
        speed = horse.get("speed", "?")
        traction = horse.get("traction", "?")
        pos = positions.get(horse_id, 0)
        prob = odds.get(horse_id, 0)

        # Progress bar
        progress = min(pos / max(distance_goal, 1), 1.0)
        bar_width = 14
        filled = int(progress * bar_width)
        bar = "[green]" + "█" * filled + "[/green]" + "░" * (bar_width - filled)

        # Medal for top 3
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")

        # Prediction market probability
        prob_str, prob_style = format_probability(prob) if prob > 0 else ("-", "dim")

        if show_stats:
            table.add_row(
                medal,
                name,
                str(speed),
                str(traction),
                f"{pos:.0f}m",
                bar,
                Text(prob_str, style=prob_style)
            )
        else:
            table.add_row(
                medal,
                name,
                f"{pos:.0f}m",
                bar,
                Text(prob_str, style=prob_style)
            )

    border_color = "green" if status == "LIVE" else "yellow"
    return Panel(
        table,
        title=f"Race {race_id} [{status}]",
        subtitle=header,
        border_style=border_color
    )


def make_winners_panel(winners: list, max_items: int = 5) -> Panel:
    """Create a panel showing recent winners."""
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("Race", style="dim", width=12)
    table.add_column("Winner", style="green")
    table.add_column("Venue", style="cyan", width=18)

    for winner in winners[:max_items]:
        table.add_row(
            winner.get("race_id", "?")[:10],
            winner.get("winner_name", "?"),
            winner.get("venue_name", "?")
        )

    if not winners:
        table.add_row("-", "No recent winners", "-")

    return Panel(table, title="Recent Winners", border_style="yellow")


def make_scheduled_panel(scheduled: list, max_items: int = 5) -> Panel:
    """Create a panel showing upcoming races."""
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("Race", style="dim", width=12)
    table.add_column("Start", style="cyan")
    table.add_column("Horses", justify="center")

    now = time.time()
    for race in scheduled[:max_items]:
        race_id = race.get("id", "?")[:10]
        start = race.get("start_time", 0)
        try:
            # Try to parse as float (UNIX timestamp), else as ISO string
            delta = float(start) - now
        except (ValueError, TypeError):
            from datetime import datetime
            try:
                race_start_time = datetime.fromisoformat(start)
                delta = race_start_time.timestamp() - now
            except Exception:
                delta = 0  # fallback on invalid
        if delta > 0:
            start_str = f"in {delta:.0f}s"
        else:
            start_str = "starting..."
        horse_count = len(race.get("horse_ids", []))
        table.add_row(race_id, start_str, str(horse_count))

    if not scheduled:
        table.add_row("-", "No scheduled races", "-")

    return Panel(table, title="Upcoming Races", border_style="blue")


def make_horses_panel(horses: dict) -> Panel:
    """Create a panel showing all horses."""
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("Name", style="cyan")
    table.add_column("Speed", justify="right")
    table.add_column("Traction", justify="right")

    for horse_id, horse in list(horses.items()):
        table.add_row(
            horse.get("name", horse_id),
            str(horse.get("speed", "?")),
            str(horse.get("traction", "?"))
        )

    return Panel(table, title=f"Horses ({len(horses)})", border_style="magenta")


def make_venues_panel(venues: dict) -> Panel:
    """Create a panel showing all venues."""
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("Name", style="cyan")
    table.add_column("Surface")
    table.add_column("Weather")
    table.add_column("Distance", justify="right")

    for venue_id, venue in venues.items():
        table.add_row(
            venue.get("name", venue_id),
            venue.get("surface", "?"),
            venue.get("weather", "?"),
            f"{venue.get('distance', '?')}m"
        )

    return Panel(table, title=f"Venues ({len(venues)})", border_style="green")


def make_stats_panel(horses: dict, venues: dict) -> Panel:
    """Create a compact stats panel for live view."""
    content = Table.grid(padding=(0, 1))

    # Horse stats summary
    if horses:
        speeds = [h.get("speed", 0) for h in horses.values()]
        tractions = [h.get("traction", 0) for h in horses.values()]
        avg_speed = sum(speeds) / len(speeds)
        avg_traction = sum(tractions) / len(tractions)
        fastest = max(horses.values(), key=lambda h: h.get("speed", 0))
        best_traction = max(horses.values(), key=lambda h: h.get("traction", 0))

        horse_table = Table(show_header=False, box=None, padding=(0, 1))
        horse_table.add_column("Label", style="dim")
        horse_table.add_column("Value", style="white")
        horse_table.add_row("Total Horses", str(len(horses)))
        horse_table.add_row("Avg Speed", f"{avg_speed:.1f}")
        horse_table.add_row("Avg Traction", f"{avg_traction:.1f}")
        horse_table.add_row("Fastest", f"{fastest.get('name', '?')[:20]} ({fastest.get('speed')})")
        horse_table.add_row("Best Grip", f"{best_traction.get('name', '?')[:20]} ({best_traction.get('traction')})")

        content.add_row(Panel(horse_table, title="Horses", border_style="magenta", padding=(0, 1)))

    # Venue stats
    if venues:
        venue_table = Table(show_header=False, box=None, padding=(0, 1))
        venue_table.add_column("Label", style="dim")
        venue_table.add_column("Value", style="white")
        venue_table.add_row("Total Venues", str(len(venues)))

        surfaces = {}
        weathers = {}
        for v in venues.values():
            s = v.get("surface", "?")
            w = v.get("weather", "?")
            surfaces[s] = surfaces.get(s, 0) + 1
            weathers[w] = weathers.get(w, 0) + 1

        venue_table.add_row("Surfaces", ", ".join(f"{k}" for k in surfaces.keys()))
        venue_table.add_row("Weather", ", ".join(f"{k}" for k in weathers.keys()))

        for _, venue in list(venues.items())[:2]:
            venue_table.add_row(
                venue.get("name", "?")[:20],
                f"{venue.get('surface')} | {venue.get('weather')} | {venue.get('distance')}m"
            )

        content.add_row(Panel(venue_table, title="Venues", border_style="green", padding=(0, 1)))

    return Panel(content, title="Stats", border_style="blue")


async def fetch_json(client: httpx.AsyncClient, endpoint: str) -> dict:
    """Fetch JSON from replica endpoint."""
    try:
        resp = await client.get(f"{REPLICA_URL}{endpoint}", timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


async def demo_static():
    """Demo: Show static state from snapshot."""
    console.print("\n[bold cyan]═══ Replica Static State ═══[/]\n")

    async with httpx.AsyncClient() as client:
        metrics = await fetch_json(client, "/metrics")
        snapshot = await fetch_json(client, "/snapshot")

        if "error" in snapshot:
            console.print(f"[red]Error: {snapshot['error']}[/]")
            return

        horses = snapshot.get("horses", {})
        venues = snapshot.get("venues", {})

        # Show metrics
        console.print(make_metrics_panel(metrics))
        console.print()

        # Show horses and venues side by side
        layout = Layout()
        layout.split_row(
            Layout(make_horses_panel(horses), name="horses"),
            Layout(make_venues_panel(venues), name="venues")
        )
        console.print(layout)
        console.print()


async def demo_live_streaming():
    """Demo: Stream live race updates via SSE."""
    console.print("\n[bold cyan]═══ Live SSE Streaming ═══[/]")
    console.print("[dim]Connecting to replica SSE stream... (Ctrl+C to stop)[/]\n")

    async with httpx.AsyncClient() as client:
        # Get initial snapshot for horse/venue data
        snapshot = await fetch_json(client, "/snapshot")
        horses = snapshot.get("horses", {})
        venues = snapshot.get("venues", {})

    update_count = 0
    start_time = time.time()

    def build_display(data: dict) -> Layout:
        """Build the live display layout."""
        layout = Layout()

        live_races = data.get("live_races", {})
        scheduled = data.get("scheduled_races", [])
        winners = data.get("recent_winners", [])
        seq = data.get("sequence_id", 0)

        # Stats line
        elapsed = time.time() - start_time
        rate = update_count / max(elapsed, 1)

        # Header with live stats
        header = Panel(
            Text(f"seq={seq} | updates={update_count} | {rate:.1f}/s | races={len(live_races)}", justify="center"),
            style="dim",
            padding=(0, 1)
        )

        # Build race panels
        race_panels = []
        for _, race in list(live_races.items())[:2]:
            race_panels.append(make_race_panel(race, horses, venues, show_stats=True))

        # Side panels
        side_content = Table.grid(padding=1)
        side_content.add_row(make_stats_panel(horses, venues))
        side_content.add_row(make_winners_panel(winners, 5))
        side_content.add_row(make_scheduled_panel(scheduled, 5))

        # Main layout
        if race_panels:
            main_content = Table.grid(padding=1)
            main_content.add_row(header)
            for panel in race_panels:
                main_content.add_row(panel)

            layout.split_row(
                Layout(main_content, name="races", ratio=2),
                Layout(side_content, name="side", ratio=1)
            )
        else:
            layout.split_row(
                Layout(Panel("Waiting for races...", border_style="dim"), ratio=2),
                Layout(side_content, ratio=1)
            )

        return layout

    async with httpx.AsyncClient(timeout=None) as client:
        try:
            async with client.stream("GET", f"{REPLICA_URL}/stream") as response:
                console.print("[green]Connected to SSE stream[/]\n")

                buffer = ""
                current_data = {}

                with Live(console=console, refresh_per_second=10) as live:
                    async for chunk in response.aiter_text():
                        buffer += chunk

                        while "\n\n" in buffer:
                            block, buffer = buffer.split("\n\n", 1)

                            for line in block.split("\n"):
                                if line.startswith("data:"):
                                    event_data = line[5:].strip()
                                    try:
                                        current_data = json.loads(event_data)
                                        update_count += 1
                                        live.update(build_display(current_data))
                                    except json.JSONDecodeError:
                                        pass

                            # Stop after 100 updates or 30 seconds
                            # if update_count >= 100 or (time.time() - start_time) > 30:
                            #     console.print(f"\n[dim]Received {update_count} updates in {time.time() - start_time:.1f}s[/]")
                            #     return

        except httpx.ConnectError:
            console.print("[red]Cannot connect to replica. Is it running on :8001?[/]")
        except KeyboardInterrupt:
            console.print(f"\n[dim]Stopped after {update_count} updates.[/]")


async def main():
    console.print(Panel.fit(
        "[bold cyan]Replica Demonstration[/]\n\n"
        "This demo showcases the replica's functionality:\n"
        "  1. Metrics with connection state, lag, and errors\n"
        "  2. Static data (horses, venues)\n"
        "  3. Live SSE streaming with race updates\n\n"
        "[dim]Requires Core on :8000 and Replica on :8001[/]",
        title="Horse Racing Replica Demo",
        border_style="cyan",
    ))

    # Check if replica is running
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{REPLICA_URL}/health", timeout=2.0)
            health = resp.json()
            console.print(f"\n[green]Replica online[/] - sequence: {health.get('sequence_id', '?')}")
        except Exception:
            console.print("\n[red]Replica not running![/]")
            console.print("Start it with: [cyan]uv run uvicorn replica.app:app --port 8001[/]")
            sys.exit(1)

    # Static demo
    await demo_static()

    # Live streaming demo
    console.print("\n[bold]Press Enter to start live SSE streaming (Ctrl+C to skip)...[/]")
    try:
        await asyncio.get_event_loop().run_in_executor(None, input)
        await demo_live_streaming()
    except KeyboardInterrupt:
        pass

    console.print("\n[bold green]Demo complete![/]\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted[/]")
