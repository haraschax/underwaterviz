#!/usr/bin/env bash
#
# grab_snapshot.sh -- Fetch a single frame from the Scripps Pier underwater
# camera and save it into a date‑partitioned directory hierarchy.
#
# This script uses ffmpeg to download one frame from the live HDOnTap
# stream and stores it in a directory structure organised by year, month
# and day. When executed, it creates directories for the current date
# (YYYY/MM/DD) under the `snapshots/` folder and writes the snapshot as
# HH.png (24‑hour clock) into that directory.

set -euo pipefail

# Base directory for snapshots relative to the repository root
BASE_DIR="$(dirname "$0")/snapshots"

# Determine today's date (UTC by default). GitHub Actions runners
# operate in UTC; set the TZ environment variable before calling
# this script if you want a different timezone (e.g.
# `TZ=America/Los_Angeles ./grab_snapshot.sh`).
YEAR="$(date +%Y)"
MONTH="$(date +%m)"
DAY="$(date +%d)"
HOUR="$(date +%H)"

# Create the directory hierarchy (e.g. snapshots/2025/07/30)
OUT_DIR="$BASE_DIR/$YEAR/$MONTH/$DAY"
mkdir -p "$OUT_DIR"

# HDOnTap HLS playlist URL for the underwater cam.  HDOnTap serves its
# streams from the `live.hdontap.com` cluster; this path comes from
# the stream name found in the page source.
STREAM_URL="https://live.hdontap.com/hls/hosb6/scripps_pier-underwater.stream/playlist.m3u8"

# Target filename (e.g. snapshots/2025/07/30/13.png)
OUT_FILE="$OUT_DIR/$HOUR.png"

# Use ffmpeg to grab a single frame.  The -y flag overwrites any
# existing file, and -loglevel error keeps the output quiet unless
# an error occurs.
ffmpeg -y -loglevel error -i "$STREAM_URL" -frames:v 1 -f image2 "$OUT_FILE"

echo "Saved snapshot to $OUT_FILE"