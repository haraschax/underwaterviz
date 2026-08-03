#!/usr/bin/env python3
"""Backfill NOAA La Jolla tide levels for every archived snapshot.

Water levels come from NOAA CO-OPS station 9410230 in feet relative to MLLW.
Requests are batched by month, while timestamps are matched to the nearest
six-minute observation within 30 minutes.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parent
SNAP_BASE = REPO_ROOT / "snapshots"
TIDES_CSV = REPO_ROOT / "docs" / "tides.csv"

NOAA_API_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
DEFAULT_STATION = "9410230"
DATUM = "MLLW"
MAX_MATCH_DISTANCE = timedelta(minutes=30)
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"

ObservationFetcher = Callable[[date, date], dict[datetime, float]]


def find_snapshot_timestamps(snap_base: Path) -> set[str]:
    """Return nominal local timestamps for YYYY/MM/DD/HH.png snapshots."""
    timestamps = set()
    if not snap_base.exists():
        return timestamps

    for image_path in snap_base.glob("*/*/*/*.png"):
        try:
            year, month, day, filename = image_path.relative_to(snap_base).parts
            hour = Path(filename).stem
            timestamp = datetime(
                int(year), int(month), int(day), int(hour), 0
            ).strftime(TIMESTAMP_FORMAT)
        except (TypeError, ValueError):
            continue
        timestamps.add(timestamp)
    return timestamps


def load_tides(csv_path: Path) -> dict[str, float]:
    """Load tide levels keyed by local timestamp."""
    tides = {}
    if not csv_path.exists():
        return tides

    with csv_path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            timestamp = row.get("timestamp", "").strip()
            try:
                tide_ft = float(row.get("tide_ft", "").strip())
            except (AttributeError, TypeError, ValueError):
                continue
            if timestamp and math.isfinite(tide_ft):
                tides[timestamp] = tide_ft
    return tides


def write_tides(csv_path: Path, tides: dict[str, float]) -> None:
    """Write tide levels in stable chronological order."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["timestamp", "tide_ft"])
        for timestamp in sorted(tides):
            writer.writerow([timestamp, f"{tides[timestamp]:.3f}"])


def fetch_noaa_observations(
    begin_date: date,
    end_date: date,
    station: str = DEFAULT_STATION,
) -> dict[datetime, float]:
    """Fetch six-minute observed water levels for an inclusive date range."""
    params = {
        "product": "water_level",
        "application": "underwaterviz",
        "begin_date": begin_date.strftime("%Y%m%d"),
        "end_date": end_date.strftime("%Y%m%d"),
        "datum": DATUM,
        "station": station,
        "time_zone": "lst_ldt",
        "units": "english",
        "format": "json",
    }
    request = Request(
        f"{NOAA_API_URL}?{urlencode(params)}",
        headers={"User-Agent": "underwaterviz tide backfill"},
    )
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)

    if payload.get("error"):
        error = payload["error"]
        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        raise RuntimeError(f"NOAA API error: {message}")

    observations = {}
    for row in payload.get("data", []):
        try:
            observed_at = datetime.strptime(row["t"], TIMESTAMP_FORMAT)
            tide_ft = float(row["v"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(tide_ft):
            observations[observed_at] = tide_ft
    return observations


def nearest_observation(
    target: datetime,
    observations: dict[datetime, float],
) -> float | None:
    """Return the closest observation when it is within the allowed window."""
    if not observations:
        return None
    observed_at = min(observations, key=lambda value: abs(value - target))
    if abs(observed_at - target) > MAX_MATCH_DISTANCE:
        return None
    return observations[observed_at]


def backfill_tides(
    snap_base: Path = SNAP_BASE,
    csv_path: Path = TIDES_CSV,
    station: str = DEFAULT_STATION,
    fetcher: ObservationFetcher | None = None,
) -> tuple[int, int, list[str]]:
    """Fill missing snapshot tides and return (added, unresolved, errors)."""
    snapshot_timestamps = find_snapshot_timestamps(snap_base)
    tides = load_tides(csv_path)
    missing = sorted(snapshot_timestamps - tides.keys())
    if not missing:
        return 0, 0, []

    grouped: dict[tuple[int, int], list[datetime]] = defaultdict(list)
    for timestamp in missing:
        target = datetime.strptime(timestamp, TIMESTAMP_FORMAT)
        grouped[(target.year, target.month)].append(target)

    if fetcher is None:
        fetcher = lambda begin, end: fetch_noaa_observations(begin, end, station)

    added = 0
    errors = []
    for (year, month), targets in sorted(grouped.items()):
        begin_date = min(target.date() for target in targets)
        end_date = max(target.date() for target in targets)
        print(f"Fetching {year}-{month:02d}: {begin_date} through {end_date}")
        try:
            observations = fetcher(begin_date, end_date)
        except Exception as error:
            message = f"{year}-{month:02d}: {error}"
            errors.append(message)
            print(f"Warning: {message}", file=sys.stderr)
            continue

        for target in targets:
            tide_ft = nearest_observation(target, observations)
            if tide_ft is None:
                continue
            tides[target.strftime(TIMESTAMP_FORMAT)] = tide_ft
            added += 1

    if added:
        write_tides(csv_path, tides)

    unresolved = len(snapshot_timestamps - tides.keys())
    return added, unresolved, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill NOAA observed tide levels for archived snapshots."
    )
    parser.add_argument("--station", default=DEFAULT_STATION)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any snapshot remains unresolved.",
    )
    args = parser.parse_args()

    added, unresolved, errors = backfill_tides(station=args.station)
    print(f"Added {added} tide levels; {unresolved} snapshots remain unresolved.")
    if args.strict and (unresolved or errors):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
