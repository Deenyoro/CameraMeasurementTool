"""Helpers for tests that drive the real Tk UI (fixtures live in
conftest.py): a tiny synthetic drawing and event helpers."""

from __future__ import annotations

import base64
import io
import time

from PIL import Image, ImageDraw

# A tiny synthetic plan: 400x250 px, a 100 px dimension line, 3 devices.
_LEGEND = [
    ("MD", "Fixed Minidome Camera",
     "ellipse;html=1;fillColor=#FFFF00;strokeColor=#0000FF;strokeWidth=2;fontSize=8"),
    ("CR", "Card Reader Door",
     "rounded=0;html=1;fillColor=#00E080;strokeColor=#0000FF;strokeWidth=1.5;fontSize=8"),
    ("IDF", "IDF / Network Rack",
     "rounded=1;html=1;fillColor=#FF4040;strokeColor=#0000FF;strokeWidth=1.5;fontSize=7"),
]
DEVICES = [("s1", "MD", 40, 40), ("s2", "MD", 300, 40), ("s3", "CR", 40, 180),
           ("s9", "IDF", 330, 190)]


def _png() -> bytes:
    im = Image.new("RGB", (400, 250), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([10, 10, 390, 240], outline="black", width=3)
    d.line([20, 230, 120, 230], fill="black", width=2)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def write_drawio(path, *, with_image: bool = True) -> str:
    style_of = {c: s for c, _l, s in _LEGEND}
    cells = ['<mxCell id="0" />', '<mxCell id="1" parent="0" />']
    if with_image:
        b64 = base64.b64encode(_png()).decode()
        cells.append(
            f'<mxCell id="bg" value="" style="shape=image;image=data:image/png,{b64};'
            f'imageAspect=0;" vertex="1" parent="1"><mxGeometry x="0" y="0" '
            f'width="400" height="250" as="geometry" /></mxCell>')
    cells.append(
        '<mxCell id="legbox" value="Symbols" style="rounded=0;html=1;fillColor=#FFFFFF;'
        'strokeColor=#0000FF" vertex="1" parent="1"><mxGeometry x="250" y="100" '
        'width="140" height="120" as="geometry" /></mxCell>')
    for i, (code, label, style) in enumerate(_LEGEND):
        y = 30 + i * 30
        cells.append(f'<mxCell id="legi{i}" value="{code}" style="{style}" vertex="1" '
                     f'parent="legbox"><mxGeometry x="8" y="{y}" width="16" height="16" '
                     f'as="geometry" /></mxCell>')
        cells.append(f'<mxCell id="legt{i}" value="{label}" style="text;html=1" vertex="1" '
                     f'parent="legbox"><mxGeometry x="30" y="{y}" width="100" height="16" '
                     f'as="geometry" /></mxCell>')
    for cid, code, x, y in DEVICES:
        cells.append(f'<mxCell id="{cid}" value="{code}" style="{style_of[code]}" '
                     f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="20" '
                     f'height="20" as="geometry" /></mxCell>')
    xml = ("<?xml version='1.0' encoding='utf-8'?>\n"
           '<mxfile><diagram id="d1" name="T"><mxGraphModel pageWidth="400" '
           'pageHeight="250"><root>' + "".join(cells)
           + "</root></mxGraphModel></diagram></mxfile>\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    return str(path)


def settle(root, ms: int = 400) -> None:
    """Let the app's deferred start-up work (first render, fit) run."""
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)


def pump(root, n: int = 3) -> None:
    for _ in range(n):
        root.update_idletasks()
        root.update()


def click(app, ix, iy, button=1):
    x, y = app._img_to_canvas(ix, iy)
    c = app.canvas
    c.event_generate("<Motion>", x=int(x), y=int(y))
    c.event_generate(f"<ButtonPress-{button}>", x=int(x), y=int(y))
    c.event_generate(f"<ButtonRelease-{button}>", x=int(x), y=int(y))


def device_img_center(app, dev_id):
    d = next(d for d in app.devices if d["id"] == dev_id)
    return app._drawio_to_img(d["x"] + d["w"] / 2, d["y"] + d["h"] / 2)
