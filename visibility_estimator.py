#!/usr/bin/env python3
"""
visibility_estimator.py — Estimate underwater visibility (in feet) from
Scripps Pier camera snapshots using OpenAI's GPT-6.1 Sol vision model.

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

from visibility_labels import INVALID_THRESHOLD, MODEL

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

"""

VALIDITY_PROMPT = """\
Separately decide whether this is an invalid capture, not just poor water visibility.
Set invalid_image=true ONLY with at least 0.98 confidence and specific visible
evidence of a stream-offline message, error/web page replacing the underwater
feed, truly blank capture, obvious camera failure, or an identifiable object
physically covering the lens. Quote any error/offline text you can actually read.
Do not invent such evidence.

Murky, dark, green, cloudy, hazy, low-contrast, or feature-poor water is NOT an
invalid image. Missing distant pilings, suspended sediment, and very low or zero
visibility are valid conditions. Blur alone is not proof of camera malfunction.
Player controls, timestamps, text overlays, or webpage borders are not grounds
for exclusion if a usable underwater scene is still visible. Do not infer an
outage or frozen stream from a single underwater frame. A dark underwater scene
is not a blank capture. When unsure, set invalid_image=false and keep the image.

invalid_confidence is your confidence (0 to 1) that there is a capture failure,
not your confidence in the visibility estimate. Use invalid_reason="none" and
invalid_evidence="" when invalid_image=false. For valid very poor visibility,
estimate 0 to 5 ft from the scene; do not substitute null for low visibility.
Use visibility_ft=null for invalid captures, or if no estimate is possible even
though there is insufficient evidence to label the capture invalid.
"""

SYSTEM_PROMPT += "\n" + VALIDITY_PROMPT

LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "visibility_ft": {"type": ["number", "null"]},
        "invalid_image": {"type": "boolean"},
        "invalid_confidence": {"type": "number"},
        "invalid_reason": {"type": "string", "enum": [
            "none", "stream_offline", "error_page", "blank_capture",
            "camera_failure", "lens_obstructed",
        ]},
        "invalid_evidence": {"type": "string"},
    },
    "required": ["analysis", "visibility_ft", "invalid_image", "invalid_confidence",
                 "invalid_reason", "invalid_evidence"],
    "additionalProperties": False,
}

USER_PROMPT = """\
Analyze this underwater camera snapshot from Scripps Pier and estimate the \
visibility in feet.

Respond in this exact JSON format (no markdown, no code fences):
{"analysis": "<brief description>", "visibility_ft": <number or null>,
 "invalid_image": <true or false>, "invalid_confidence": <0 to 1>,
 "invalid_reason": "<reason enum>", "invalid_evidence": "<specific evidence or empty>"}\
"""

REVIEW_PROMPT = """\
Review this Scripps Pier camera snapshot for capture validity. Decide from the
image alone whether it shows a genuine underwater scene, including very poor
visibility, or a clearly invalid capture.\n""" + VALIDITY_PROMPT + """\n
Return the requested JSON. Use visibility_ft=null; this review is only about
capture validity. Explain the visible evidence briefly in analysis.
"""


def invalid_candidate(result):
    return (result["invalid_image"] and INVALID_THRESHOLD <= result["invalid_confidence"] <= 1
            and result["invalid_reason"] != "none" and bool(result["invalid_evidence"].strip()))


def finalize_label(result, review=None):
    result = dict(result)
    confirmed = invalid_candidate(result) and review is not None and invalid_candidate(review)
    result["invalid_image"] = bool(confirmed)
    result["invalid_confirmed"] = bool(confirmed)
    result["model"] = MODEL
    if review is not None:
        result["invalid_confidence"] = min(result["invalid_confidence"], review["invalid_confidence"])
    if confirmed:
        result["visibility_ft"] = None
    else:
        result["invalid_reason"] = "none"
        result["invalid_evidence"] = ""
    return result


def _encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def classify_snapshot(image_path):
    """Estimate visibility and confirm any proposed invalid capture separately."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return failed_label("OPENAI_API_KEY not set")

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

    try:
        result = _api_request(client, SYSTEM_PROMPT, content)
        review = None
        if invalid_candidate(result):
            review = _api_request(client, REVIEW_PROMPT, [content[-1]])
        return finalize_label(result, review)
    except Exception as error:
        print(f"  Visibility estimation failed: {error}", file=sys.stderr)
        return failed_label(f"error: {error}")


def failed_label(message):
    return {
        "visibility_ft": None, "analysis": message, "invalid_image": False,
        "invalid_confirmed": False, "invalid_confidence": 0,
        "invalid_reason": "none", "invalid_evidence": "", "model": "",
    }


def _api_request(client, prompt, content):
    for attempt in range(5):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                reasoning_effort="low",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": content},
                ],
                max_completion_tokens=5000,
                response_format={"type": "json_schema", "json_schema": {
                    "name": "visibility_label", "strict": True, "schema": LABEL_SCHEMA,
                }},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            err_str = str(e)
            if attempt < 4 and ("429" in err_str or "rate_limit" in err_str.lower()):
                wait = 2 ** attempt + 1
                print(f"  Rate limited, retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise


def estimate_visibility(image_path):
    result = classify_snapshot(image_path)
    value = result["visibility_ft"]
    return (float("nan") if value is None else value), result["analysis"]


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

        result = classify_snapshot(path)
        print(f"{path}")
        print(f"  Visibility: {result['visibility_ft']} ft")
        print(f"  Invalid capture: {result['invalid_image']}")
        print(f"  Analysis: {result['analysis']}")
        print()


if __name__ == "__main__":
    main()
