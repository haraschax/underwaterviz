#!/usr/bin/env bash
#
# grab_snapshot.sh -- Fetch frames from the Scripps Pier underwater camera
# and maintain a curated archive. This script is designed to be executed
# hourly via cron or a GitHub Actions workflow. It performs three tasks:
#
# 1. Capture a single frame from the live stream and save it into a
#    date‑partitioned directory hierarchy under `snapshots/YYYY/MM/DD/HH.png`.
#    The capture only occurs if the current hour falls within the allowed
#    time window. The time window is defined once via START_HOUR and
#    END_HOUR variables (inclusive). For example, START_HOUR=6 and
#    END_HOUR=19 will allow captures from 06:00 through 19:59.
#
# 2. Remove any previously saved snapshots that lie outside of the allowed
#    time window. This ensures that the repository only contains images
#    captured during the desired hours, keeping the data set compact.
#
# 3. Generate a set of "last 7 days" highlight images. For each of the
#    last seven calendar days, the script selects the snapshot whose hour
#    is closest to noon (12:00) within the allowed time window. These
#    highlights are copied into `docs/last7days/` and a JSON manifest
#    (`last7days.json`) is written. The website can fetch this manifest
#    to display recent images without performing API calls or time‑window
#    logic on the client.

set -euo pipefail

# Base directory for snapshots relative to the repository root
BASE_DIR="$(dirname "$0")/snapshots"

# Directory for the last 7 days highlights under docs
LAST7_DIR="$(dirname "$0")/docs/last7days"

# Define the inclusive time window for capturing snapshots. Only hours
# between START_HOUR and END_HOUR (inclusive) will be saved. These
# variables should be defined in one place to avoid duplication.
START_HOUR=6
END_HOUR=19

# Determine current date components. Set TZ externally (e.g.
# TZ=America/Los_Angeles) if desired.
YEAR="$(date +%Y)"
MONTH="$(date +%m)"
DAY="$(date +%d)"
HOUR="$(date +%H)"

# Create today's directory hierarchy (e.g. snapshots/2025/07/30)
OUT_DIR="$BASE_DIR/$YEAR/$MONTH/$DAY"
mkdir -p "$OUT_DIR"

# Only capture a snapshot if the current hour is within the allowed range.
# Convert the HH string into a decimal number (strip leading zero) to avoid
# octal interpretation in arithmetic contexts.
current_hour=$((10#$HOUR))
if (( current_hour >= START_HOUR && current_hour <= END_HOUR )); then

  EMBED_URL="https://portal.hdontap.com/s/embed/?streamKey=scripps_pier-underwater"
  STREAM_URL=$(curl -fsSL "$EMBED_URL" | grep -oP '"streamSrc":"\K[^"]+')
  printf -v STREAM_URL '%b' "$STREAM_URL"
  echo "STREAM_URL=$STREAM_URL"
  if [[ -n "$STREAM_URL" ]]; then
    OUT_FILE="$OUT_DIR/$HOUR.png"
    # Capture one frame from the current stream URL
    ffmpeg -y -loglevel error -i "$STREAM_URL" -frames:v 1 -f image2 "$OUT_FILE"
    echo "Saved snapshot to $OUT_FILE"
  else
    echo "Warning: failed to fetch stream URL from embed page; snapshot not saved."
  fi

else
  echo "Current hour $HOUR is outside the allowed time window ($START_HOUR-$END_HOUR); snapshot not saved."
fi

# Delete snapshots outside the allowed time window. Iterate over all
# existing PNG files under snapshots and remove any whose hour component
# falls outside of [START_HOUR, END_HOUR].
find "$BASE_DIR" -type f -name '*.png' | while read -r file; do
  fname="$(basename "$file" .png)"
  # If the filename isn't purely numeric, skip it
  if [[ "$fname" =~ ^[0-9]{1,2}$ ]]; then
    # Strip any leading zero to avoid octal interpretation
    hour_num=$((10#$fname))
    if (( hour_num < START_HOUR || hour_num > END_HOUR )); then
      rm -f "$file"
    fi
  fi
done

# Prepare the last7days highlights directory
mkdir -p "$LAST7_DIR"
rm -f "$LAST7_DIR"/*.png "$LAST7_DIR"/last7days.json || true

# Build JSON manifest and copy the best snapshot for each of the last 7 days
manifest="["
count=0
for offset in {0..6}; do
  target_date="$(date -d "-$offset day" +%Y-%m-%d)"
  Y="$(date -d "-$offset day" +%Y)"
  M="$(date -d "-$offset day" +%m)"
  D="$(date -d "-$offset day" +%d)"
  day_dir="$BASE_DIR/$Y/$M/$D"
  if [ -d "$day_dir" ]; then
    best_file=""
    best_diff=1000
    for img in "$day_dir"/*.png; do
      [ -e "$img" ] || continue
      hour="$(basename "$img" .png)"
      # Skip if not numeric
      if ! [[ "$hour" =~ ^[0-9]{1,2}$ ]]; then continue; fi
      # Convert to decimal to avoid octal interpretation
      hour_num=$((10#$hour))
      # Check time window
      if (( hour_num < START_HOUR || hour_num > END_HOUR )); then
        continue
      fi
      # Compute absolute difference from noon (12)
      diff=$(( hour_num > 12 ? hour_num - 12 : 12 - hour_num ))
      if (( diff < best_diff )); then
        best_file="$img"
        best_diff=$diff
      fi
    done
    if [ -n "$best_file" ]; then
      hr="$(basename "$best_file" .png)"
      out_name="${Y}-${M}-${D}_${hr}.png"
      cp "$best_file" "$LAST7_DIR/$out_name"
      # Append to JSON manifest
      if [ "$count" -gt 0 ]; then
        manifest+="," 
      fi
      manifest+="{\"file\":\"$out_name\",\"date\":\"${Y}-${M}-${D}\",\"time\":\"$hr\"}"
      count=$((count + 1))
    fi
  fi
done
manifest+="]"
echo "$manifest" > "$LAST7_DIR/last7days.json"
echo "Wrote highlights manifest to $LAST7_DIR/last7days.json"

# -----------------------------------------------------------------------------
# Generate a months manifest.  This manifest lists all year/month
# combinations that currently contain at least one snapshot.  Only months
# containing at least one PNG file are included.  The time window logic
# does not need to be rechecked here because files outside the allowed
# window have already been removed by the cleanup step above.
#
# The result is written to docs/months.json as an array of objects with
# "year" and "month" keys, for example: [{"year":"2025","month":"07"}, ...].

MONTHS_MANIFEST_FILE="$(dirname "$0")/docs/months.json"
months_json="["
month_count=0
shopt -s nullglob
for year_dir in "$BASE_DIR"/*; do
  [[ -d "$year_dir" ]] || continue
  year_name="$(basename "$year_dir")"
  for month_dir in "$year_dir"/*; do
    [[ -d "$month_dir" ]] || continue
    month_name="$(basename "$month_dir")"
    # Check if this month contains at least one image file
    has_files=0
    # Look for any PNG inside day directories
    if compgen -G "$month_dir"/*/*.png > /dev/null; then
      has_files=1
    fi
    if [ "$has_files" -eq 1 ]; then
      if [ "$month_count" -gt 0 ]; then
        months_json+="," 
      fi
      months_json+="{\"year\":\"$year_name\",\"month\":\"$month_name\"}"
      month_count=$((month_count + 1))
    fi
  done
done
months_json+="]"
echo "$months_json" > "$MONTHS_MANIFEST_FILE"
echo "Wrote months manifest to $MONTHS_MANIFEST_FILE"
