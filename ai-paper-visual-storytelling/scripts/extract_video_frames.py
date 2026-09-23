#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Extract review frames from a demo video for AI paper figures."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path


FRAME_GLOB = "frame-*.jpg"


def run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing executable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed with exit code {exc.returncode}: {' '.join(command)}") from exc


def run_capture(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing executable: {command[0]}") from exc
    if result.returncode != 0:
        raise SystemExit(f"Command failed with exit code {result.returncode}: {' '.join(command)}")
    return f"{result.stdout}\n{result.stderr}"


def parse_showinfo_timestamps(output: str) -> list[float]:
    return [float(match.group(1)) for match in re.finditer(r"pts_time:([0-9.]+)", output)]


def build_frame_manifest(frame_files: list[str], timestamps: list[float], args: argparse.Namespace) -> list[dict]:
    if len(timestamps) != len(frame_files) and args.scene_threshold is None:
        timestamps = [index * args.every_sec for index in range(len(frame_files))]
    return [
        {
            "file": frame_file,
            "timestamp_sec": round(timestamps[index], 3) if index < len(timestamps) else None,
        }
        for index, frame_file in enumerate(frame_files)
    ]


def probe(video: Path) -> dict:
    if not shutil.which("ffprobe"):
        return {}

    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return {"ffprobe_error": result.stderr.strip()}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ffprobe_error": "invalid json output"}


def build_filter(args: argparse.Namespace) -> str:
    filters: list[str] = []
    if args.scene_threshold is not None:
        threshold = max(0.0, min(args.scene_threshold, 1.0))
        filters.append(f"select='gt(scene,{threshold})'")
    else:
        every_sec = max(args.every_sec, 0.1)
        filters.append(f"fps=1/{every_sec}")

    if args.max_width:
        filters.append(f"scale='min({args.max_width},iw)':-2")
    return ",".join(filters)


def create_contact_sheet(frames_dir: Path, columns: int, max_width: int | None) -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("Missing executable: ffmpeg")

    frame_count = len(list(frames_dir.glob(FRAME_GLOB)))
    if frame_count == 0:
        return
    rows = max(1, math.ceil(frame_count / columns))
    width = max_width or 320
    output = frames_dir / "contact-sheet.jpg"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-pattern_type",
        "glob",
        "-i",
        str(frames_dir / FRAME_GLOB),
        "-vf",
        f"scale={width}:-1,tile={columns}x{rows}",
        "-frames:v",
        "1",
        str(output),
    ]
    run(command)


def clear_previous_outputs(out_dir: Path) -> None:
    for frame_file in out_dir.glob(FRAME_GLOB):
        frame_file.unlink()
    for generated_file in (out_dir / "contact-sheet.jpg", out_dir / "frames-manifest.json"):
        if generated_file.exists():
            generated_file.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract candidate frames and metadata from a demo video for paper figure storyboards."
    )
    parser.add_argument("video", type=Path, help="Input video or screen recording.")
    parser.add_argument("--out", type=Path, required=True, help="Output directory for frames and metadata.")
    parser.add_argument("--every-sec", type=float, default=2.0, help="Extract one frame every N seconds.")
    parser.add_argument("--scene-threshold", type=float, help="Use ffmpeg scene-change selection instead of fixed interval.")
    parser.add_argument("--max-width", type=int, default=1600, help="Scale frames to this max width while preserving ratio.")
    parser.add_argument("--contact-sheet", action="store_true", help="Also create contact-sheet.jpg from extracted frames.")
    parser.add_argument("--contact-columns", type=int, default=4, help="Columns for the contact sheet.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    video = args.video.expanduser().resolve()
    out_dir = args.out.expanduser().resolve()

    if not video.is_file():
        print(f"Video not found: {video}", file=sys.stderr)
        return 1
    if not shutil.which("ffmpeg"):
        print("Missing executable: ffmpeg", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    clear_previous_outputs(out_dir)
    frame_pattern = out_dir / "frame-%04d.jpg"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-y",
        "-i",
        str(video),
        "-vf",
        f"{build_filter(args)},showinfo",
        "-vsync",
        "vfr",
        str(frame_pattern),
    ]
    ffmpeg_output = run_capture(command)
    frame_files = sorted(path.name for path in out_dir.glob(FRAME_GLOB))

    metadata = {
        "source_video": str(video),
        "output_dir": str(out_dir),
        "mode": "scene" if args.scene_threshold is not None else "interval",
        "every_sec": args.every_sec if args.scene_threshold is None else None,
        "scene_threshold": args.scene_threshold,
        "max_width": args.max_width,
        "probe": probe(video),
        "frames": build_frame_manifest(frame_files, parse_showinfo_timestamps(ffmpeg_output), args),
    }
    (out_dir / "frames-manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    if args.contact_sheet and metadata["frames"]:
        create_contact_sheet(out_dir, max(args.contact_columns, 1), 320)

    print(json.dumps({"out": str(out_dir), "frame_count": len(metadata["frames"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
