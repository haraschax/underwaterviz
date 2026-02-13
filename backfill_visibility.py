#!/usr/bin/env python3
"""Backfill visibility estimates for a given month into docs/visibility.csv.

Uses parallel API calls for speed (default 10 workers).
"""

import csv
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# Load .env
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from visibility_estimator import estimate_visibility

VIS_CSV = REPO_ROOT / "docs" / "visibility.csv"
csv_lock = threading.Lock()


def load_existing_timestamps():
    """Load already-estimated timestamps from CSV."""
    existing = set()
    if VIS_CSV.exists():
        with open(VIS_CSV, newline="") as f:
            for row in csv.DictReader(f):
                existing.add(row.get("timestamp", "").strip())
    return existing


def append_row(timestamp, vis_ft, analysis):
    with csv_lock:
        write_header = not VIS_CSV.exists() or VIS_CSV.stat().st_size == 0
        with open(VIS_CSV, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["timestamp", "visibility_ft", "conditions"])
            vis_str = "" if (vis_ft != vis_ft) else str(vis_ft)  # NaN check
            writer.writerow([timestamp, vis_str, analysis])


def process_image(img_path, year, month, idx, total):
    day = img_path.parent.name
    hour = img_path.stem.zfill(2)
    timestamp = f"{year}-{month}-{day} {hour}:00"
    vis_ft, analysis = estimate_visibility(str(img_path))
    append_row(timestamp, vis_ft, analysis)
    print(f"  [{idx}/{total}] {timestamp} — ~{vis_ft} ft", flush=True)
    return timestamp, vis_ft


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <year> <month> [workers]")
        print(f"Example: {sys.argv[0]} 2026 01 10")
        sys.exit(1)

    year, month = sys.argv[1], sys.argv[2].zfill(2)
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    snap_dir = REPO_ROOT / "snapshots" / year / month

    if not snap_dir.exists():
        print(f"No snapshots found at {snap_dir}")
        sys.exit(1)

    existing = load_existing_timestamps()

    images = sorted(snap_dir.rglob("*.png"))
    total = len(images)
    print(f"Found {total} images in {year}/{month}")

    # Filter out already-estimated
    to_process = []
    for img_path in images:
        day = img_path.parent.name
        hour = img_path.stem.zfill(2)
        timestamp = f"{year}-{month}-{day} {hour}:00"
        if timestamp not in existing:
            to_process.append(img_path)

    skipped = total - len(to_process)
    if skipped:
        print(f"Skipping {skipped} already-estimated images")
    print(f"Processing {len(to_process)} images with {workers} parallel workers...")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for i, img_path in enumerate(to_process):
            fut = executor.submit(process_image, img_path, year, month, i + 1, len(to_process))
            futures[fut] = img_path

        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                fut.result()
            except Exception as e:
                print(f"  Error processing {futures[fut]}: {e}", flush=True)

    print(f"Done! Processed {done} images.")


if __name__ == "__main__":
    main()
