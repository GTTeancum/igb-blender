"""Encode Nintendo GX RGB5A3 in big-endian 4x4 tiles."""
import struct


def encode_rgb5a3(rgba, width, height):
    """Encode row-major RGBA8 pixels as big-endian GX 4x4 tiles.

    Opaque pixels use RGB555; other pixels use A3RGB444. Dimensions must
    describe complete tiles, and the buffer must contain exactly RGBA8 data.
    """
    if width <= 0 or height <= 0 or width % 4 or height % 4:
        raise ValueError("RGB5A3 requires positive dimensions divisible by four")
    if len(rgba) != width * height * 4:
        raise ValueError("RGB5A3 input must contain width * height * 4 bytes")
    data = bytearray()
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            for y in range(by, by + 4):
                for x in range(bx, bx + 4):
                    i = (y * width + x) * 4
                    r, g, b, a = rgba[i:i + 4]
                    if a >= 224:
                        value = 0x8000 | (r >> 3) << 10 | (g >> 3) << 5 | (b >> 3)
                    else:
                        value = ((a >> 5) << 12 | (r >> 4) << 8 |
                                 (g >> 4) << 4 | (b >> 4))
                    data.extend(struct.pack(">H", value))
    return bytes(data)
