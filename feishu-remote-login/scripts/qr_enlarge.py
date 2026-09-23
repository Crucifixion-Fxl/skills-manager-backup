#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""放大二维码截图，让它能从聊天消息里被手机扫到：最近邻放大 N 倍，再加一圈白边（quiet zone）。

只用标准库：无头主机上通常没有 Pillow / ImageMagick / ffmpeg。
支持 8 位 RGB / RGBA、非隔行的 PNG（Chromium 截图就是这种）；其它格式明确报错，不悄悄输出坏图。
"""
import argparse
import json
import struct
import sys
import zlib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != PNG_MAGIC:
        raise SystemExit(f"{path}: not a PNG")
    pos, idat, header = 8, b"", None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in (2, 6) or interlace:
                raise SystemExit(f"{path}: unsupported PNG (bit depth {depth}, colour type {ctype}, interlace {interlace}); need 8-bit RGB/RGBA, not interlaced")
            header = (width, height, 4 if ctype == 6 else 3)
        elif kind == b"IDAT":
            idat += body
    if header is None:
        raise SystemExit(f"{path}: no IHDR chunk")
    width, height, bpp = header
    raw, stride, rows, prev = zlib.decompress(idat), width * bpp, [], bytearray(width * bpp)
    for y in range(height):
        base = y * (stride + 1)
        ftype, line = raw[base], bytearray(raw[base + 1:base + 1 + stride])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 255
            elif ftype == 2:
                line[i] = (line[i] + b) & 255
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
            elif ftype != 0:
                raise SystemExit(f"{path}: bad PNG filter type {ftype}")
        rows.append(bytes(line))
        prev = line
    return width, height, bpp, rows


def write_png(path, width, height, rows):
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    with open(path, "wb") as f:
        f.write(PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def enlarge(width, height, bpp, rows, scale, border):
    out_w = width * scale + 2 * border
    white = b"\xff\xff\xff"
    blank = white * out_w
    out = [blank] * border
    for row in rows:
        pixels = b"".join(row[x * bpp:x * bpp + 3] * scale for x in range(width))  # drop alpha, repeat each pixel
        line = white * border + pixels + white * border
        out.extend([line] * scale)
    out.extend([blank] * border)
    return out_w, len(out), out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--scale", type=int, default=4, help="integer enlargement factor (default 4)")
    ap.add_argument("--border", type=int, default=48, help="white border in output pixels (default 48)")
    args = ap.parse_args(argv)
    if args.scale < 1 or args.border < 0:
        ap.error("--scale must be >= 1 and --border >= 0")
    try:
        width, height, bpp, rows = read_png(args.src)
        out_w, out_h, out = enlarge(width, height, bpp, rows, args.scale, args.border)
        write_png(args.dst, out_w, out_h, out)
    except (zlib.error, struct.error, IndexError, OSError) as exc:
        raise SystemExit(f"{args.src}: cannot process this PNG ({type(exc).__name__}: {exc})")
    print(json.dumps({"src": args.src, "from": f"{width}x{height}", "dst": args.dst, "to": f"{out_w}x{out_h}"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
