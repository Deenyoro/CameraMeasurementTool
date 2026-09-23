"""Generate the Camera Wire Tool application icon.

Run from the repo root (needs Pillow, already a runtime dependency):

    python packaging/make_icon.py

Writes:
    packaging/icons/cameramtool.png   256x256, used by the Linux AppImage
    packaging/icons/cameramtool.ico   16..256 px, used by the Windows exe
                                      (camera_wire_tool.spec) and installer

Both outputs are committed so builds never depend on running this script;
re-run it only when the design changes. The glyph is a flat, high-contrast
security camera over a measuring rule, drawn on a 1024 px canvas and
downsampled with LANCZOS so every size stays crisp.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "icons"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]

S = 1024  # master canvas size

BG = (22, 44, 74, 255)        # deep navy tile
BODY = (244, 247, 250, 255)   # near-white camera body
LENS_RING = (22, 44, 74, 255)
LENS = (0, 168, 204, 255)     # teal accent
GLINT = (244, 247, 250, 255)
RULE = (255, 184, 28, 255)    # amber measuring rule


def draw_master() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square tile.
    d.rounded_rectangle((32, 32, S - 32, S - 32), radius=200, fill=BG)

    # Camera body (bullet-style housing) with a sunshade lip on top.
    d.rounded_rectangle((170, 250, 760, 600), radius=70, fill=BODY)
    d.rounded_rectangle((150, 215, 800, 285), radius=35, fill=BODY)

    # Lens: navy ring, teal glass, small glint.
    cx, cy, r = 760, 425, 150
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=BODY)
    r2 = 112
    d.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), fill=LENS_RING)
    r3 = 78
    d.ellipse((cx - r3, cy - r3, cx + r3, cy + r3), fill=LENS)
    d.ellipse((cx - 50, cy - 50, cx - 10, cy - 10), fill=GLINT)

    # Mount arm down to the rule.
    d.rectangle((400, 600, 470, 690), fill=BODY)

    # Measuring rule with tick marks.
    d.rounded_rectangle((150, 690, 874, 830), radius=28, fill=RULE)
    x0, x1 = 200, 824
    n = 8
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n
        h = 80 if i % 4 == 0 else (58 if i % 2 == 0 else 38)
        d.rectangle((x - 9, 690, x + 9, 690 + h), fill=BG)
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    master = draw_master()
    png = master.resize((256, 256), Image.LANCZOS)
    png.save(OUT_DIR / "cameramtool.png", optimize=True)
    # Pillow builds each ICO frame by downsampling the image it is given, so
    # hand it the 1024 px master rather than the 256 px PNG.
    master.save(OUT_DIR / "cameramtool.ico", sizes=[(n, n) for n in ICO_SIZES])
    print(f"wrote {OUT_DIR / 'cameramtool.png'} and {OUT_DIR / 'cameramtool.ico'}")


if __name__ == "__main__":
    main()
