"""Asset-free encoder tests: python -m unittest discover -s tests."""
import importlib.util
from pathlib import Path
import struct
import unittest


path = Path(__file__).resolve().parents[1] / "utils" / "wii_rgb5a3.py"
spec = importlib.util.spec_from_file_location("wii_rgb5a3", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
encode = module.encode_rgb5a3


class RGB5A3Tests(unittest.TestCase):
    def test_opaque_colors_and_big_endian(self):
        for rgba, word in [
            ((255, 0, 0, 255), 0xFC00),
            ((0, 255, 0, 255), 0x83E0),
            ((0, 0, 255, 255), 0x801F),
            ((255, 255, 255, 255), 0xFFFF),
            ((0, 0, 0, 255), 0x8000),
        ]:
            with self.subTest(rgba=rgba):
                self.assertEqual(encode(bytes(rgba) * 16, 4, 4),
                                 struct.pack(">H", word) * 16)

    def test_alpha_branch_boundary(self):
        for alpha, word in [(0, 0x0FFF), (128, 0x4FFF),
                            (223, 0x6FFF), (224, 0xFFFF)]:
            with self.subTest(alpha=alpha):
                self.assertEqual(encode(bytes((255, 255, 255, alpha)) * 16, 4, 4),
                                 struct.pack(">H", word) * 16)

    def test_tile_order_is_not_linear_scanline(self):
        # Four tiles, each with varying pixels: catches swapped tile axes and
        # reversed within-tile scan order, rather than only testing solid colors.
        pixels = bytes(v for y in range(8) for x in range(8)
                       for v in (x * 8, y * 8, 0, 255))
        expected = [0x8000 | (x << 10) | (y << 5)
                    for by, bx in [(0, 0), (0, 4), (4, 0), (4, 4)]
                    for y in range(by, by + 4) for x in range(bx, bx + 4)]
        self.assertEqual(encode(pixels, 8, 8), struct.pack(">64H", *expected))

    def test_non_square_tiles(self):
        pixels = b"".join(bytes((255, 0, 0, 255) if x < 4 else (0, 255, 0, 255))
                          for y in range(4) for x in range(8))
        self.assertEqual(encode(pixels, 8, 4), b"\xfc\x00" * 16 + b"\x83\xe0" * 16)

    def test_invalid_dimensions(self):
        for width, height in [(0, 4), (4, 0), (-4, 4), (2, 4), (4, 6)]:
            with self.subTest(width=width, height=height):
                with self.assertRaises(ValueError):
                    encode(b"", width, height)

    def test_exact_input_length_required(self):
        for size in (0, 63, 65):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    encode(bytes(size), 4, 4)


if __name__ == "__main__":
    unittest.main()
