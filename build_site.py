#!/usr/bin/env python3
"""Build monthly image indexes and direct-entry pages for GitHub Pages."""

import calendar
import csv
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from visibility_labels import is_invalid


REPO_ROOT = Path(__file__).resolve().parent


def load_measurements(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(newline="") as stream:
        return {row["timestamp"].strip()[:13]: row for row in csv.DictReader(stream)}


def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_last7days(snap_base, docs_dir, months, today=None):
    today = today or datetime.now(ZoneInfo("America/Los_Angeles")).date()
    last7_dir = docs_dir / "last7days"
    last7_dir.mkdir(exist_ok=True)
    manifest = []
    for offset in range(7):
        day = today - timedelta(days=offset)
        year, month, date = day.strftime("%Y %m %d").split()
        candidates = [image for image in months.get((year, month), []) if image["day"] == date]
        if not candidates:
            continue
        best = min(candidates, key=lambda image: (abs(int(Path(image["file"]).stem) - 12), image["file"]))
        hour = Path(best["file"]).stem
        filename = f"{day.isoformat()}_{hour}.png"
        shutil.copy2(snap_base / year / month / date / best["file"], last7_dir / filename)
        manifest.append({
            "date": day.isoformat(), "time": hour, "file": filename,
            "visibility_ft": best["visibility_ft"], "conditions": best["conditions"],
            "tide_ft": best["tide_ft"], "invalid_image": False,
        })
    keep = {image["file"] for image in manifest}
    for old in last7_dir.glob("*.png"):
        if old.name not in keep:
            old.unlink()
    (last7_dir / "last7days.json").write_text(json.dumps(manifest, allow_nan=False) + "\n")


def build_site(snap_base: Path, docs_dir: Path) -> None:
    visibility = load_measurements(docs_dir / "visibility.csv")
    tides = load_measurements(docs_dir / "tides.csv")
    months = {}
    for path in sorted(snap_base.glob("*/*/*/*.png")):
        year, month, day, filename = path.relative_to(snap_base).parts
        try:
            timestamp = datetime(int(year), int(month), int(day), int(path.stem))
        except ValueError:
            continue
        if not 6 <= timestamp.hour < 20:
            continue
        key = timestamp.strftime("%Y-%m-%d %H")
        measurement = visibility.get(key, {})
        images = months.setdefault((year, month), [])
        if is_invalid(measurement):
            continue
        images.append({
            "day": day,
            "file": filename,
            "visibility_ft": finite_number(measurement.get("visibility_ft")),
            "conditions": measurement.get("conditions", ""),
            "tide_ft": finite_number(tides.get(key, {}).get("tide_ft")),
        })

    manifest_dir = docs_dir / "months"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    page = (docs_dir / "index.html").read_text()
    for (year, month), images in months.items():
        images.sort(key=lambda image: (int(image["day"]), int(Path(image["file"]).stem)))
        manifest = manifest_dir / f"{year}-{month}.json"
        manifest.write_text(json.dumps(images, separators=(",", ":"), allow_nan=False) + "\n")
        route_dir = docs_dir / f"{year}{calendar.month_abbr[int(month)].lower()}"
        route_dir.mkdir(exist_ok=True)
        (route_dir / "index.html").write_text(page)

    month_list = [{"year": year, "month": month} for (year, month), images in months.items() if images]
    (docs_dir / "months.json").write_text(json.dumps(month_list))
    chart_data = {key: {
        "visibility_ft": None if is_invalid(row) else finite_number(row.get("visibility_ft")),
    } for key, row in visibility.items()}
    (docs_dir / "visibility.json").write_text(json.dumps(chart_data, separators=(",", ":"), allow_nan=False) + "\n")
    build_last7days(snap_base, docs_dir, months)
    print(f"Built {len(months)} months with {sum(map(len, months.values()))} images.")


if __name__ == "__main__":
    build_site(REPO_ROOT / "snapshots", REPO_ROOT / "docs")
