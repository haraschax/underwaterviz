#!/usr/bin/env python3
"""Relabel the snapshot archive through a ChatGPT-authenticated Codex CLI."""

import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from visibility_estimator import (
    GOOD_VIS_IMAGE, GREAT_VIS_IMAGE, LABELED_IMAGE, LABEL_SCHEMA,
    REVIEW_PROMPT, SYSTEM_PROMPT, USER_PROMPT, finalize_label, invalid_candidate,
)
from visibility_labels import MODEL, label_row, save_labels


REPO_ROOT = Path(__file__).resolve().parent
REFERENCES = [LABELED_IMAGE, GREAT_VIS_IMAGE, GOOD_VIS_IMAGE]
PROMPT = SYSTEM_PROMPT + "\n\n" + USER_PROMPT + """\n
Exactly four images are attached in order: (1) labeled distance diagram,
(2) 35 ft reference, (3) 25 ft reference, (4) the snapshot to evaluate.
Evaluate ONLY image 4. All information needed is included. Do not use tools,
read files, or modify files. Return only the requested JSON.
"""
REVIEW = REVIEW_PROMPT + "\nDo not use tools, read files, or modify files."


def validate_label(result):
    if set(result) != set(LABEL_SCHEMA["required"]):
        raise ValueError("Incomplete label")
    if type(result["invalid_image"]) is not bool:
        raise ValueError("Invalid image flag is not boolean")
    if not isinstance(result["invalid_confidence"], (float, int)) or not 0 <= result["invalid_confidence"] <= 1:
        raise ValueError("Invalid confidence")
    value = result["visibility_ft"]
    if value is not None and (not isinstance(value, (float, int)) or not 0 <= value <= 100):
        raise ValueError("Invalid visibility")
    if result["invalid_reason"] not in LABEL_SCHEMA["properties"]["invalid_reason"]["enum"]:
        raise ValueError("Invalid reason")
    if not all(isinstance(result[key], str) for key in ["analysis", "invalid_evidence"]):
        raise ValueError("Invalid evidence")


class Relabeler:
    def __init__(self, output):
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        self.schema = output / "schema.json"
        self.schema.write_text(json.dumps(LABEL_SCHEMA))
        self.fingerprint = hashlib.sha256((MODEL + PROMPT + REVIEW + self.schema.read_text()).encode())
        for reference in REFERENCES:
            self.fingerprint.update(reference.read_bytes())
        self.fingerprint = self.fingerprint.hexdigest()
        self.invalid_by_hash = {}
        for checkpoint in output.glob("*.label.json"):
            prior = json.loads(checkpoint.read_text())
            if prior["prompt_sha256"] == self.fingerprint and prior["label"]["invalid_image"] and not prior.get("reused_from"):
                self.invalid_by_hash.setdefault(prior["image_sha256"], prior)
        (output / "prompt.txt").write_text(PROMPT)
        (output / "review-prompt.txt").write_text(REVIEW)
        self.env = dict(os.environ)
        for key in ["OPENAI_API_KEY", "CODEX_API_KEY"]:
            self.env.pop(key, None)
        self.completed = 0
        self.cached = 0
        self.failed = []
        self.stopped = False
        self.started = time.monotonic()

    async def request(self, identifier, images, prompt):
        checkpoint = self.output / f"{identifier}.json"
        if checkpoint.exists():
            return json.loads(checkpoint.read_text())
        for attempt in range(5):
            answer_path = self.output / f"{identifier}.answer.json"
            answer_path.unlink(missing_ok=True)
            command = [
                "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                "--model", MODEL, "-c", 'model_reasoning_effort="low"',
                "-c", 'service_tier="default"', "-c", 'approval_policy="never"',
                "-c", "mcp_servers.openaiDeveloperDocs.enabled=false",
                "--json", "--output-schema", str(self.schema), "-o", str(answer_path),
            ]
            for image in images:
                command.extend(["--image", str(image)])
            command.append("-")
            started = time.monotonic()
            proc = await asyncio.create_subprocess_exec(
                *command, cwd=self.output, env=self.env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=180)
            except asyncio.TimeoutError:
                proc.kill()
                stdout, stderr = await proc.communicate()
            events = []
            for line in stdout.decode(errors="replace").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            (self.output / f"{identifier}.attempt-{attempt}.events.jsonl").write_bytes(stdout)
            (self.output / f"{identifier}.attempt-{attempt}.stderr.txt").write_bytes(stderr)
            usage = next((event.get("usage") for event in events if event.get("type") == "turn.completed"), None)
            tool_uses = [event for event in events if event.get("item", {}).get("type") in
                         ["command_execution", "mcp_tool_call", "web_search"]]
            try:
                if proc.returncode or usage is None or tool_uses:
                    raise ValueError(f"Codex failed: returncode={proc.returncode}, usage={usage is not None}, tools={len(tool_uses)}")
                result = json.loads(answer_path.read_text())
                validate_label(result)
                data = {"answer": result, "usage": usage, "elapsed_seconds": time.monotonic() - started}
                checkpoint.write_text(json.dumps(data))
                return data
            except (ValueError, OSError) as error:
                if attempt == 4:
                    raise RuntimeError(str(error)) from error
                print(f"Retry {identifier}: {error}", flush=True)
                await asyncio.sleep(min(30, 2 ** (attempt + 1)))

    async def label(self, image):
        relative = image.relative_to(REPO_ROOT / "snapshots")
        identifier = "-".join(relative.with_suffix("").parts)
        image_hash = hashlib.sha256(image.read_bytes()).hexdigest()
        expected = {"image_sha256": image_hash, "prompt_sha256": self.fingerprint}
        result_path = self.output / f"{identifier}.label.json"
        if result_path.exists():
            prior = json.loads(result_path.read_text())
            if all(prior.get(key) == value for key, value in expected.items()):
                review = prior["review"]
                prior["label"] = finalize_label(prior["primary"]["answer"], review["answer"] if review else None)
                result_path.write_text(json.dumps(prior))
                self.cached += 1
                self.completed += 1
                return prior
        source = self.invalid_by_hash.get(image_hash)
        if source is not None:
            result = copy.deepcopy(source)
            result.update(path=str(relative), labeled_at=datetime.now(timezone.utc).isoformat(),
                          reused_from=source["path"], reuse_reason="identical_image_sha256")
            for call in [result["primary"], result["review"]]:
                call["usage"] = {key: 0 for key in call["usage"]}
                call["elapsed_seconds"] = 0
            result_path.write_text(json.dumps(result))
            self.cached += 1
            self.completed += 1
            return result
        request_id = f"{identifier}-{image_hash[:8]}-{self.fingerprint[:8]}"
        primary = await self.request(request_id, [*REFERENCES, image], PROMPT)
        review = None
        if invalid_candidate(primary["answer"]):
            review = await self.request(request_id + "-review", [image], REVIEW)
        label = finalize_label(primary["answer"], review["answer"] if review else None)
        result = {
            "path": str(relative), **expected, "model": MODEL,
            "labeled_at": datetime.now(timezone.utc).isoformat(),
            "primary": primary, "review": review, "label": label,
        }
        result_path.write_text(json.dumps(result))
        if label["invalid_image"]:
            self.invalid_by_hash.setdefault(image_hash, result)
        self.completed += 1
        if self.completed % 25 == 0:
            self.progress()
        return result

    def progress(self):
        elapsed = time.monotonic() - self.started
        print(f"Completed {self.completed}/{self.total}; cached {self.cached}; errors {len(self.failed)}; {elapsed / 60:.1f} min", flush=True)

    async def run(self, images, workers):
        self.total = len(images)
        queue = asyncio.Queue()
        for image in images:
            queue.put_nowait(image)

        async def worker():
            while not queue.empty() and not self.stopped:
                image = queue.get_nowait()
                try:
                    await self.label(image)
                except Exception as error:
                    self.failed.append({"path": str(image), "error": str(error)})
                    print(f"FAILED {image}: {error}", flush=True)
                    if len(self.failed) >= 3:
                        self.stopped = True
                finally:
                    queue.task_done()

        await asyncio.gather(*(worker() for _ in range(workers)))
        self.progress()
        if self.completed != self.total:
            raise RuntimeError("Run incomplete; checkpoints retained. Resume after addressing failures.")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--samples", type=Path, help="Benchmark-style JSON list of image paths")
    parser.add_argument("--apply", action="store_true", help="Replace visibility.csv after the complete archive finishes")
    args = parser.parse_args()
    images = sorted((REPO_ROOT / "snapshots").glob("*/*/*/*.png"))
    if args.samples:
        if args.apply:
            parser.error("--apply requires the complete archive")
        images = sorted({Path(sample["path"]) for sample in json.loads(args.samples.read_text())})
    relabeler = Relabeler(args.output.resolve())
    await relabeler.run(images, args.workers)
    if args.apply:
        rows = {}
        for image in images:
            relative = image.relative_to(REPO_ROOT / "snapshots")
            identifier = "-".join(relative.with_suffix("").parts)
            result = json.loads((relabeler.output / f"{identifier}.label.json").read_text())
            year, month, day, filename = relative.parts
            timestamp = f"{year}-{month}-{day} {Path(filename).stem.zfill(2)}:00"
            rows[timestamp[:13]] = label_row(timestamp, result["label"])
        destination = REPO_ROOT / "docs" / "visibility.csv"
        backup = relabeler.output / "visibility-before.csv"
        if not backup.exists():
            shutil.copy2(destination, backup)
        save_labels(destination, rows)
        print(f"Applied {len(rows)} labels to {destination}; original saved to {backup}")


if __name__ == "__main__":
    asyncio.run(main())
