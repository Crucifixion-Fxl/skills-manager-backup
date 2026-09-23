"""qr_enlarge.py：标准库 PNG 放大器。每个 PNG 过滤器都要能正确解码，放大后的像素位置和白边要对。"""
import importlib.util
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "qr_enlarge.py")
spec = importlib.util.spec_from_file_location("qr_enlarge", SCRIPT)
qe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qe)


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def encode(width, height, ctype, rows, filters=None, interlace=0, depth=8):
    """Build a PNG from raw pixel rows, applying a chosen filter to each row (0 none, 1 Sub, 2 Up, 3 Average, 4 Paeth)."""
    bpp = 4 if ctype == 6 else 3
    filters = filters or [0] * height
    out, prev = b"", bytes(width * bpp)
    for y, row in enumerate(rows):
        f, line = filters[y], bytearray(row)
        for i in range(len(row) - 1, -1, -1):  # filter from the right so earlier bytes stay raw
            a = row[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (row[i] - a) & 255
            elif f == 2:
                line[i] = (row[i] - b) & 255
            elif f == 3:
                line[i] = (row[i] - ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (row[i] - (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += bytes([f]) + bytes(line)
        prev = row
    ihdr = struct.pack(">IIBBBBB", width, height, depth, ctype, 0, 0, interlace)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(out)) + chunk(b"IEND", b"")


def pattern(width, height, bpp):
    return [bytes((x * 37 + y * 11 + c * 53) & 255 for x in range(width) for c in range(bpp)) for y in range(height)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def write(self, name, data):
        with open(self.path(name), "wb") as f:
            f.write(data)
        return self.path(name)


class DecodeTests(Base):
    def test_every_filter_type_round_trips_for_rgb_and_rgba(self):
        for ctype, bpp in ((2, 3), (6, 4)):
            rows = pattern(7, 5, bpp)
            for f in (0, 1, 2, 3, 4):
                with self.subTest(colour_type=ctype, filter=f):
                    src = self.write("in.png", encode(7, 5, ctype, rows, [f] * 5))
                    w, h, got_bpp, got = qe.read_png(src)
                    self.assertEqual((w, h, got_bpp), (7, 5, bpp))
                    self.assertEqual(got, rows)

    def test_mixed_filters_per_row(self):
        rows = pattern(6, 5, 4)
        src = self.write("in.png", encode(6, 5, 6, rows, [0, 1, 2, 3, 4]))
        self.assertEqual(qe.read_png(src)[3], rows)

    def test_a_png_written_by_a_real_encoder_decodes_to_the_known_pixels(self):
        """tests/fixtures/chromium-quadrants-64.png was screenshotted by Chromium (Skia/libpng, filters Sub+Up+Paeth): red, green /
        blue, black quadrants. Unlike encode() above, this oracle does not share any code with the decoder."""
        w, h, bpp, rows = qe.read_png(os.path.join(HERE, "fixtures", "chromium-quadrants-64.png"))
        self.assertEqual((w, h, bpp), (64, 64, 3))
        px = lambda x, y: tuple(rows[y][x * bpp:x * bpp + 3])
        self.assertEqual([px(10, 10), px(50, 10), px(10, 50), px(50, 50)], [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)])
        self.assertEqual({px(x, y) for x in range(32) for y in range(32)}, {(255, 0, 0)})

    def test_image_data_split_over_several_idat_chunks_is_joined(self):
        rows = pattern(9, 7, 3)
        whole = encode(9, 7, 2, rows)
        pos, idat = 8, b""
        while pos < len(whole):
            n, kind = struct.unpack(">I4s", whole[pos:pos + 8])
            if kind == b"IDAT":
                idat = whole[pos + 8:pos + 8 + n]
            pos += 12 + n
        third = len(idat) // 3
        parts = b"".join(chunk(b"IDAT", part) for part in (idat[:third], idat[third:2 * third], idat[2 * third:]))
        png = whole[:33] + parts + chunk(b"IEND", b"")
        self.assertEqual(qe.read_png(self.write("split.png", png))[3], rows)

    def test_larger_seeded_random_images_round_trip_for_every_filter(self):
        import random
        rng = random.Random(7)
        for ctype, bpp in ((2, 3), (6, 4)):
            rows = [bytes(rng.randrange(256) for _ in range(40 * bpp)) for _ in range(30)]
            for f in (1, 2, 3, 4):
                with self.subTest(colour_type=ctype, filter=f):
                    self.assertEqual(qe.read_png(self.write("r.png", encode(40, 30, ctype, rows, [f] * 30)))[3], rows)

    def test_unsupported_inputs_are_rejected_not_silently_mangled(self):
        cases = {
            "not a png": b"GIF89a....",
            "palette": encode(2, 2, 3, [b"\x00\x00", b"\x00\x00"]),
            "interlaced": encode(2, 2, 2, pattern(2, 2, 3), interlace=1),
            "16 bit": encode(2, 2, 2, pattern(2, 2, 3), depth=16),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(SystemExit):
                qe.read_png(self.write("bad.png", data))


class EnlargeTests(Base):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_size_pixels_and_white_border(self):
        rows = pattern(4, 3, 4)  # RGBA with varying alpha: the output must drop alpha and keep RGB
        src = self.write("in.png", encode(4, 3, 6, rows, [0, 2, 4]))
        dst = self.path("out.png")
        r = self.run_cli(src, dst, "--scale", "3", "--border", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        w, h, bpp, out = qe.read_png(dst)
        self.assertEqual((w, h, bpp), (4 * 3 + 10, 3 * 3 + 10, 3))
        for y in range(3):
            for x in range(4):
                expected = rows[y][x * 4:x * 4 + 3]
                for dy in range(3):
                    for dx in range(3):
                        px = 5 + x * 3 + dx
                        self.assertEqual(out[5 + y * 3 + dy][px * 3:px * 3 + 3], expected, (x, y, dx, dy))
        white = b"\xff\xff\xff"
        self.assertTrue(all(out[y] == white * w for y in list(range(5)) + list(range(h - 5, h))))
        self.assertTrue(all(row[:15] == white * 5 and row[-15:] == white * 5 for row in out))

    def test_default_scale_is_four_with_48_px_border(self):
        src = self.write("in.png", encode(10, 10, 2, pattern(10, 10, 3)))
        r = self.run_cli(src, self.path("out.png"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(qe.read_png(self.path("out.png"))[:2], (10 * 4 + 96, 10 * 4 + 96))
        self.assertIn('"to":"136x136"', r.stdout)

    def test_scale_one_and_border_zero_are_valid_and_change_nothing_but_size(self):
        rows = pattern(4, 4, 3)
        src = self.write("in.png", encode(4, 4, 2, rows))
        self.assertEqual(self.run_cli(src, self.path("o.png"), "--scale", "1", "--border", "0").returncode, 0)
        self.assertEqual(qe.read_png(self.path("o.png"))[3], rows)

    def test_bad_arguments_fail_with_usage_error(self):
        src = self.write("in.png", encode(2, 2, 2, pattern(2, 2, 3)))
        self.assertEqual(self.run_cli(src, self.path("o.png"), "--scale", "0").returncode, 2)
        self.assertEqual(self.run_cli(src, self.path("o.png"), "--border", "-1").returncode, 2)

    def test_corrupt_png_gives_a_clean_one_line_error_not_a_traceback(self):
        good = encode(4, 4, 2, pattern(4, 4, 3))
        cases = {"truncated": good[:len(good) // 2], "bad zlib stream": good.replace(b"IDAT", b"IDAT", 1)[:-30] + b"\x00" * 30, "empty": b""}
        for name, data in cases.items():
            with self.subTest(name):
                r = self.run_cli(self.write("bad.png", data), self.path("o.png"))
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn("Traceback", r.stderr)
                self.assertTrue(r.stderr.strip(), "an error message is expected")

    def test_output_is_one_json_line(self):
        import json
        src = self.write("in.png", encode(3, 3, 2, pattern(3, 3, 3)))
        out = json.loads(self.run_cli(src, self.path("o.png")).stdout)
        self.assertEqual(out["from"], "3x3")

    def test_help_exits_zero(self):
        self.assertEqual(self.run_cli("--help").returncode, 0)


if __name__ == "__main__":
    unittest.main()
