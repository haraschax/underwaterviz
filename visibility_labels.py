import csv
import math
import os
from pathlib import Path


MODEL = os.environ.get("VISIBILITY_MODEL", "gpt-6.1-sol")
INVALID_THRESHOLD = 0.98
FIELDS = [
    "timestamp", "visibility_ft", "conditions", "invalid_image",
    "invalid_confidence", "invalid_reason", "invalid_evidence",
    "invalid_confirmed", "model",
]


def is_invalid(row):
    return str(row.get("invalid_image", "")).lower() == "true" and (
        str(row.get("invalid_confirmed", "")).lower() == "true"
        and float(row.get("invalid_confidence") or 0) >= INVALID_THRESHOLD
    )


def load_labels(path):
    if not Path(path).exists():
        return {}
    with open(path, newline="") as stream:
        return {row["timestamp"].strip()[:13]: row for row in csv.DictReader(stream)}


def save_labels(path, rows):
    path = Path(path)
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows[key] for key in sorted(rows))
    temporary.replace(path)


def label_row(timestamp, result):
    vis = result["visibility_ft"]
    return {
        "timestamp": timestamp[:13] + ":00",
        "visibility_ft": vis if vis is not None and math.isfinite(vis) else "",
        "conditions": result["analysis"],
        "invalid_image": str(result["invalid_image"]).lower(),
        "invalid_confidence": result["invalid_confidence"],
        "invalid_reason": result["invalid_reason"],
        "invalid_evidence": result["invalid_evidence"],
        "invalid_confirmed": str(result.get("invalid_confirmed", False)).lower(),
        "model": result.get("model", MODEL),
    }


def append_label(path, timestamp, result):
    rows = load_labels(path)
    rows[timestamp[:13]] = label_row(timestamp, result)
    save_labels(path, rows)
