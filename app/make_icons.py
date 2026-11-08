"""Makes static/icon-256.png and static/icon.ico from static/icon.svg (the full drawing) and static/icon-small.svg
(thicker strokes, for 16 to 32 px). Run after a change of the logo; needs GdkPixbuf (librsvg) and Pillow.

The ICO holds its sizes below 256 as 32-bit bitmaps (with their AND mask) and only its 256 px frame as PNG: the
classic layout every Windows reader takes, System.Drawing and old shell code included (some of them show PNG frames
of small sizes as noise)."""
from __future__ import annotations

import io
import struct
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(svg: Path, n: int):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    from PIL import Image
    ok, buf = GdkPixbuf.Pixbuf.new_from_file_at_size(str(svg), n, n).save_to_bufferv("png", [], [])
    return Image.open(io.BytesIO(buf)).convert("RGBA")


def bmp_frame(im) -> bytes:
    """A 32-bit BGRA DIB, bottom-up, its height doubled for the AND mask that follows (all zero: the alpha decides)."""
    w, h = im.size
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    px = im.transpose(1).tobytes("raw", "BGRA")  # FLIP_TOP_BOTTOM
    mask_row = ((w + 31) // 32) * 4
    return header + px + b"\0" * (mask_row * h)


def ico(frames: dict[int, object]) -> bytes:
    data = []
    for n, im in frames.items():
        if n >= 256:
            b = io.BytesIO()
            im.save(b, "PNG", optimize=True)
            data.append(b.getvalue())
        else:
            data.append(bmp_frame(im))
    out = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    for (n, _), d in zip(frames.items(), data):
        out += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(d), offset)
        offset += len(d)
    return out + b"".join(data)


def main() -> None:
    full, small = STATIC / "icon.svg", STATIC / "icon-small.svg"
    frames = {n: render(small if n <= 32 and small.is_file() else full, n) for n in SIZES}
    frames[256].save(STATIC / "icon-256.png", optimize=True)
    (STATIC / "icon.ico").write_bytes(ico(frames))
    print(f"made {STATIC / 'icon-256.png'} and {STATIC / 'icon.ico'} ({', '.join(map(str, SIZES))})")


if __name__ == "__main__":
    main()
