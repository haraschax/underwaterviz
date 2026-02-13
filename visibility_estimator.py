#!/usr/bin/env python3
"""
visibility_estimator.py — Estimate underwater visibility (in feet) from
Scripps Pier camera snapshots using OpenAI's gpt-5.1 vision model.

Can be used as a module:
    from visibility_estimator import estimate_visibility
    vis_ft, analysis = estimate_visibility("path/to/image.png")

Or as a standalone CLI:
    python visibility_estimator.py <image_path> [image_path ...]
"""

import sys
import os
import base64
import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
LABELED_IMAGE = REPO_ROOT / "reference" / "labeled_viz.png"
GREAT_VIS_IMAGE = REPO_ROOT / "reference" / "great_visibility_35ft.png"
GOOD_VIS_IMAGE = REPO_ROOT / "reference" / "good_visibility_25ft.png"

SYSTEM_PROMPT = """\
You are an expert marine biologist and underwater visibility analyst for the \
Scripps Pier underwater camera in La Jolla, California.

The camera is fixed at ~4m (13ft) depth under Scripps Pier, looking through \
the pier pilings. The pilings serve as distance markers:

- Closest piling (right edge): ~4 ft (1.2m) from camera
- Mid-right piling: ~11 ft (3.4m) from camera
- Back-left piling: ~14 ft (4.3m) from camera
- Farthest visible pilings (center-left): ~30 ft (9m) from camera

You will be shown three reference images before the image to evaluate:
1. A labeled diagram showing which piling is at 4ft, 11ft, 14ft, and 30ft (~25ft visibility).
2. A ~35ft exceptional visibility image where all pilings are sharp with texture and the sandy bottom is visible.
3. A ~25ft good visibility image where the 30ft pilings are faintly visible as silhouettes.
Use these to calibrate your estimates.

Visibility estimation guidelines (use the FULL range, do not round conservatively):
- If the 30ft pilings are clearly visible with sharp texture AND you can see \
the sandy bottom: 35 ft
- If the 30ft pilings are mostly visible, but less clear than the reference: 30ft
- If the 30ft pilings are faintly visible as silhouettes: 25ft
- If the 14ft piling is sharp with visible texture: 20 ft
- If the 14ft piling is hazy/faded silhouette: 15 ft
- If only the 11ft piling is visible: 10 ft
- If only the closest 4ft piling is clear: 5ft
- If barely anything is visible: <5 ft

Clearly go through the steps above. Think clearly.

IMPORTANT: If the image is NOT a valid underwater snapshot (e.g., error page, \
offline message, webpage screenshot, completely black frame, camera malfunction, \
animal blocking the lens, or anything else that prevents a reliable visibility \
reading), you MUST set visibility_ft to "nan".\
"""

USER_PROMPT = """\
Analyze this underwater camera snapshot from Scripps Pier and estimate the \
visibility in feet.

Respond in this exact JSON format (no markdown, no code fences):
{"analysis": "<brief description>", "visibility_ft": <number or "nan">}\
"""


def _encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def estimate_visibility(image_path):
    """Estimate underwater visibility from a Scripps Pier snapshot.

    Returns (visibility_ft, analysis) where visibility_ft is a float or NaN.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return float("nan"), "OPENAI_API_KEY not set"

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    b64 = _encode_image(image_path)
    suffix = Path(image_path).suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"

    content = []

    # 1) Labeled diagram
    if LABELED_IMAGE.exists():
        content.append({"type": "text", "text": "Labeled diagram (~25ft visibility) showing piling distances from camera:"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{_encode_image(LABELED_IMAGE)}"},
        })

    # 2) Great visibility (~35ft)
    if GREAT_VIS_IMAGE.exists():
        content.append({"type": "text", "text": "Reference: ~35ft exceptional visibility. All pilings sharp with texture, sandy bottom visible:"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{_encode_image(GREAT_VIS_IMAGE)}"},
        })

    # 3) Good visibility (~25ft)
    if GOOD_VIS_IMAGE.exists():
        content.append({"type": "text", "text": "Reference: ~25ft good visibility. 30ft pilings faintly visible as silhouettes:"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{_encode_image(GOOD_VIS_IMAGE)}"},
        })

    content.append({"type": "text", "text": USER_PROMPT})
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{b64}"},
    })

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-5.1",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                max_completion_tokens=5000,
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            result = json.loads(raw)
            vis = result.get("visibility_ft")
            analysis = result.get("analysis", "")

            if vis is None or str(vis).lower() == "nan":
                return float("nan"), analysis
            return float(vis), analysis

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str.lower():
                wait = 2 ** attempt + 1
                print(f"  Rate limited, retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  Visibility estimation failed: {e}", file=sys.stderr)
            return float("nan"), f"error: {e}"

    print(f"  Exhausted retries for {image_path}", file=sys.stderr)
    return float("nan"), "error: rate limit retries exhausted"


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <image_path> [image_path ...]")
        sys.exit(1)

    # Load .env if present
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    for path in sys.argv[1:]:
        if not Path(path).exists():
            print(f"{path}: File not found")
            continue

        vis_ft, analysis = estimate_visibility(path)
        print(f"{path}")
        print(f"  Visibility: ~{vis_ft} ft")
        print(f"  Analysis: {analysis}")
        print()


if __name__ == "__main__":
    main()
