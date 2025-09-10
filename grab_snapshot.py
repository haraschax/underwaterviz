#!/usr/bin/env python3
"""
grab_snapshot.py — Python replacement for the underwaterviz grab_snapshot.sh

Features:
- Capture a single PNG into snapshots/YYYY/MM/DD/HH.png during allowed hours.
- Remove snapshots outside the allowed time window.
- Build docs/last7days/* and docs/last7days.json (closest-to-noon for last 7 days).
- Build docs/months.json listing months with snapshots.

Env/flags:
  URL (or --url)              : page to open (default: https://coollab.ucsd.edu/pierviz/)
  START_HOUR, END_HOUR        : inclusive hours to capture (defaults 6..19)
  HEADLESS                    : 'false' to show Chrome
  TZ                          : respected by `datetime.now()` if your system honors it

Exit codes:
  0 on success (even if outside time window and no capture done).
  Non-zero if a capture was attempted but failed to produce a file.
"""

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
import time
import sys
import shutil

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ----------------------- Config / Paths -----------------------

REPO_ROOT = Path(__file__).resolve().parent
SNAP_BASE = REPO_ROOT / "snapshots"
LAST7_DIR = REPO_ROOT / "docs" / "last7days"
MONTHS_MANIFEST_FILE = REPO_ROOT / "docs" / "months.json"

DEFAULT_URL = "https://coollab.ucsd.edu/pierviz/"
DEFAULT_START = int(os.environ.get("START_HOUR", "6"))
DEFAULT_END = int(os.environ.get("END_HOUR", "19"))


# ----------------------- Selenium helpers -----------------------

def _make_driver(headless: bool) -> webdriver.Chrome:
    opts = Options()
    if headless:
        # Use new headless if supported
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--hide-scrollbars")
    return webdriver.Chrome(options=opts)


def _try_video_screenshot(driver: webdriver.Chrome, out_path: Path, timeout: int = 10) -> bool:
    """Try to screenshot a <video> element in the current browsing context."""
    try:
        video = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, "video"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", video)
        time.sleep(0.3)  # small settle
        video.screenshot(str(out_path))
        return True
    except Exception:
        return False


def capture_snapshot(url: str, out_path: Path, headless: bool = True) -> None:
    """Open the page, try all contexts for a <video>, else full-page screenshot."""
    driver = _make_driver(headless)
    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(5)  # allow player to hydrate

        took = False

        # 1) Top-level
        if _try_video_screenshot(driver, out_path):
            took = True

        # 2) Each iframe
        if not took:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for i in range(len(iframes)):
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(i)
                    if _try_video_screenshot(driver, out_path):
                        took = True
                        break
                except Exception:
                    continue
            driver.switch_to.default_content()

        # 3) Fallback full page
        if not took:
            try:
                height = driver.execute_script(
                    "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 1080)"
                )
                driver.set_window_size(1920, max(int(height), 1080))
            except Exception:
                pass
            driver.save_screenshot(str(out_path))

        # Sanity check
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError("Snapshot file missing or empty after capture.")

    finally:
        driver.quit()


# ----------------------- Repo ops (clean, manifests) -----------------------

def within_window(hour: int, start_h: int, end_h: int) -> bool:
    return start_h <= hour <= end_h


def clean_outside_window(snap_base: Path, start_h: int, end_h: int) -> None:
    """Delete snapshots whose filename hour is outside the allowed window."""
    if not snap_base.exists():
        return
    for p in snap_base.rglob("*.png"):
        try:
            h_str = p.stem
            if not h_str.isdigit():
                continue
            hour_num = int(h_str, 10)
            if not within_window(hour_num, start_h, end_h):
                p.unlink(missing_ok=True)
        except Exception:
            # Best effort—ignore malformed files
            continue


def build_last7days(snap_base: Path, last7_dir: Path, start_h: int, end_h: int) -> None:
    """Pick closest-to-noon snapshot for each of last 7 days and write manifest."""
    last7_dir.mkdir(parents=True, exist_ok=True)
    # clear old
    for old in last7_dir.glob("*.png"):
        old.unlink(missing_ok=True)
    (last7_dir / "last7days.json").unlink(missing_ok=True)

    manifest = []
    noon = 12

    now = datetime.now()
    for offset in range(0, 7):
        day = now - timedelta(days=offset)
        Y = day.strftime("%Y")
        M = day.strftime("%m")
        D = day.strftime("%d")
        day_dir = snap_base / Y / M / D
        if not day_dir.is_dir():
            continue

        best_file = None
        best_diff = 10**9

        for img in day_dir.glob("*.png"):
            h_str = img.stem
            if not h_str.isdigit():
                continue
            h = int(h_str, 10)
            if not within_window(h, start_h, end_h):
                continue
            diff = abs(h - noon)
            if diff < best_diff:
                best_diff = diff
                best_file = img

        if best_file:
            out_name = f"{Y}-{M}-{D}_{best_file.stem}.png"
            shutil.copy2(best_file, last7_dir / out_name)
            manifest.append({
                "file": out_name,
                "date": f"{Y}-{M}-{D}",
                "time": best_file.stem
            })

    with open(last7_dir / "last7days.json", "w") as f:
        json.dump(manifest, f)
    

def build_months_manifest(snap_base: Path, out_file: Path) -> None:
    """List months (year, month) that contain at least one snapshot PNG."""
    months = []
    if snap_base.exists():
        for year_dir in sorted([d for d in snap_base.iterdir() if d.is_dir()]):
            year = year_dir.name
            for month_dir in sorted([d for d in year_dir.iterdir() if d.is_dir()]):
                month = month_dir.name
                # any PNG in any day dir?
                has_files = any(p.suffix == ".png" for p in month_dir.rglob("*.png"))
                if has_files:
                    months.append({"year": year, "month": month})
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(months, f)


# ----------------------- CLI -----------------------

def main():
    parser = argparse.ArgumentParser(description="Grab a snapshot and update manifests.")
    parser.add_argument("--url", default=os.environ.get("URL", DEFAULT_URL),
                        help=f"Page to open (default: {DEFAULT_URL})")
    parser.add_argument("--start-hour", type=int, default=DEFAULT_START,
                        help=f"Inclusive start hour (default: {DEFAULT_START})")
    parser.add_argument("--end-hour", type=int, default=DEFAULT_END,
                        help=f"Inclusive end hour (default: {DEFAULT_END})")
    parser.add_argument("--headless", default=os.environ.get("HEADLESS", "true"),
                        help="Set to 'false' to show browser (default: true)")
    args = parser.parse_args()

    headless = str(args.headless).lower() not in ("0", "false", "no")

    now = datetime.now()
    Y = now.strftime("%Y")
    M = now.strftime("%m")
    D = now.strftime("%d")
    H = now.strftime("%H")
    hour_num = int(H, 10)

    out_dir = SNAP_BASE / Y / M / D
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{H}.png"

    # Capture only within window
    if within_window(hour_num, args.start_hour, args.end_hour):
        try:
            capture_snapshot(args.url, out_file, headless=headless)
            if not out_file.exists() or out_file.stat().st_size == 0:
                print(f"Error: snapshot not saved to {out_file}", file=sys.stderr)
                sys.exit(1)
            print(f"Saved snapshot to {out_file}")
        except Exception as e:
            print(f"Error while capturing snapshot: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Current hour {H} outside window ({args.start_hour}-{args.end_hour}); not capturing.")

    # Housekeeping: remove outside-window files, then rebuild manifests
    clean_outside_window(SNAP_BASE, args.start_hour, args.end_hour)
    build_last7days(SNAP_BASE, LAST7_DIR, args.start_hour, args.end_hour)
    build_months_manifest(SNAP_BASE, MONTHS_MANIFEST_FILE)
    print("Updated docs/last7days and docs/months.json")

if __name__ == "__main__":
    main()