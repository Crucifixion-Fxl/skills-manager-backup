#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow==12.2.0"]
# ///
"""Create a size-bounded WebP delivery asset without manual compression."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageOps


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=825)
    parser.add_argument("--height", type=int, default=660)
    parser.add_argument("--max-kib", type=int, default=200)
    parser.add_argument("--quality", type=int, default=88)
    parser.add_argument("--min-quality", type=int, default=76)
    parser.add_argument("--quality-step", type=int, default=2)
    parser.add_argument("--resize", action="store_true")
    return parser.parse_args()


def emit(status: str, **values: object) -> None:
    print(json.dumps({"status": status, **values}, ensure_ascii=False, sort_keys=True))


def main() -> int:
    args = parse_args()
    if args.width <= 0 or args.height <= 0 or args.max_kib <= 0:
        emit("invalid_arguments")
        return 2
    if not (0 <= args.min_quality <= args.quality <= 100) or args.quality_step <= 0:
        emit("invalid_quality_range")
        return 2
    if not args.input.is_file():
        emit("input_not_found", input=str(args.input))
        return 2

    try:
        with Image.open(args.input) as source:
            source.load()
            image = ImageOps.exif_transpose(source).copy()
    except Exception as error:  # Pillow reports format-specific decode errors.
        emit("decode_failed", error=str(error))
        return 2

    expected = (args.width, args.height)
    if image.size != expected:
        if not args.resize:
            emit("dimension_mismatch", actual=list(image.size), expected=list(expected))
            return 2
        image = ImageOps.fit(image, expected, method=Image.Resampling.LANCZOS)

    has_alpha = "A" in image.getbands() or "transparency" in image.info
    image = image.convert("RGBA" if has_alpha else "RGB")
    limit = args.max_kib * 1024
    qualities = list(range(args.quality, args.min_quality - 1, -args.quality_step))
    if qualities[-1] != args.min_quality:
        qualities.append(args.min_quality)

    selected: tuple[int, bytes] | None = None
    smallest: tuple[int, int] | None = None
    for quality in qualities:
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=quality, method=6, exact=has_alpha)
        payload = buffer.getvalue()
        if smallest is None or len(payload) < smallest[1]:
            smallest = (quality, len(payload))
        if len(payload) <= limit:
            selected = (quality, payload)
            break

    if selected is None:
        emit(
            "size_limit_not_met",
            limit_bytes=limit,
            smallest_bytes=smallest[1] if smallest else None,
            smallest_quality=smallest[0] if smallest else None,
        )
        return 2

    quality, payload = selected
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, args.output)

    with Image.open(args.output) as result:
        result.load()
        if result.size != expected or result.format != "WEBP":
            emit("output_validation_failed")
            return 2

    emit(
        "ok",
        output=str(args.output),
        width=args.width,
        height=args.height,
        quality=quality,
        bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
