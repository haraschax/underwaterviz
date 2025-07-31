#!/usr/bin/env bash
#
# port_snapshots.sh -- Migrate existing snapshot directories from the old
# YYYY‑MM‑DD layout to the new YYYY/MM/DD layout.
#
# Prior to 2025‑07‑31 this repository stored snapshots in a single
# directory per day (e.g. `snapshots/2025-07-30/13.png`).  Starting on
# 2025‑07‑31 snapshots are stored in a nested year/month/day hierarchy
# (e.g. `snapshots/2025/07/31/13.png`).  Run this script once to
# restructure all existing directories into the new format.  The script
# is idempotent – running it multiple times will not move files that
# already follow the new layout.

set -euo pipefail

# Base directory for snapshots relative to the repository root
BASE_DIR="$(dirname "$0")/snapshots"

shopt -s nullglob

for datedir in "$BASE_DIR"/*; do
  # Only operate on directories
  [[ -d "$datedir" ]] || continue
  dir_name=$(basename "$datedir")
  # Match directories of the form YYYY-MM-DD
  if [[ "$dir_name" =~ ^([0-9]{4})-([0-9]{2})-([0-9]{2})$ ]]; then
    year="${BASH_REMATCH[1]}"
    month="${BASH_REMATCH[2]}"
    day="${BASH_REMATCH[3]}"
    new_dir="$BASE_DIR/$year/$month/$day"
    # Skip if the directory already follows the new layout
    if [[ "$datedir" == "$new_dir" ]]; then
      continue
    fi
    mkdir -p "$new_dir"
    for file in "$datedir"/*; do
      [[ -f "$file" ]] || continue
      mv "$file" "$new_dir/"
    done
    # Remove the old directory if it's empty
    rmdir "$datedir"
  fi
done