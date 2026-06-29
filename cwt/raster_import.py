"""Render vector/page drawings (PDF, SVG, DXF) into a .drawio document.

The app is built entirely around .drawio files whose floor plan is a PNG
embedded as a base64 ``shape=image`` cell (see ``parse_drawio`` in
``camera_wire_tool``). To let users start from a PDF / SVG / DXF, we
rasterize the source to a PNG and wrap it in a minimal .drawio whose page
size equals the image pixel size — so drawio units map 1:1 to image
pixels and the app's coordinate transforms need no special-casing.

Heavy/optional renderers (PyMuPDF for PDF+SVG, ezdxf+matplotlib for DXF)
are imported lazily inside each function so importing this module never
forces those deps, and a missing one yields a clear message for just the
format that needs it.
"""

from __future__ import annotations

import base64
import io
import os
from xml.sax.saxutils import quoteattr

from PIL import Image

# Long-edge pixel budget for the rasterized background. Big enough that
# architectural line work and room labels stay legible when zoomed in,
# small enough to keep the embedded PNG (and the .zsmt blob) reasonable.
DEFAULT_TARGET_LONG_PX = 5500
# Guard rails so a tiny icon SVG isn't blown up to a 5500px canvas and a
# giant sheet isn't shrunk into mush.
MIN_ZOOM = 0.25
MAX_ZOOM = 4.0

SUPPORTED_EXTS = (".pdf", ".svg", ".dxf")


def _zoom_for(width_units: float, height_units: float,
              target_long_px: int) -> float:
    """Scale factor that brings the longer source edge to ~target_long_px,
    clamped so we never up/down-scale absurdly."""
    longest = max(width_units, height_units)
    if longest <= 0:
        return 1.0
    return max(MIN_ZOOM, min(MAX_ZOOM, target_long_px / longest))


def render_pdf_to_image(path: str, *, page: int = 0,
                        target_long_px: int = DEFAULT_TARGET_LONG_PX) -> Image.Image:
    """Rasterize one page of a PDF to an RGB PIL image (white background)."""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "Opening PDF floor plans needs PyMuPDF.\n"
            "Install it with:  pip install pymupdf") from e
    doc = fitz.open(path)
    try:
        if doc.page_count == 0:
            raise RuntimeError("PDF has no pages.")
        pg = doc[max(0, min(page, doc.page_count - 1))]
        zoom = _zoom_for(pg.rect.width, pg.rect.height, target_long_px)
        # alpha=False → flatten onto white so transparent PDFs don't come
        # through with a black background the device icons disappear into.
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return img.copy()  # detach from the freed pixmap buffer
    finally:
        doc.close()


def render_svg_to_image(path: str, *,
                        target_long_px: int = DEFAULT_TARGET_LONG_PX) -> Image.Image:
    """Rasterize an SVG to an RGB PIL image via PyMuPDF (SVG → PDF → pixmap)."""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "Opening SVG floor plans needs PyMuPDF.\n"
            "Install it with:  pip install pymupdf") from e
    svg_doc = fitz.open(path)
    try:
        # PyMuPDF reads SVG as a 1-page document; convert to PDF first so we
        # get a proper page rect to scale against, then rasterize.
        pdf_bytes = svg_doc.convert_to_pdf()
    finally:
        svg_doc.close()
    doc = fitz.open("pdf", pdf_bytes)
    try:
        pg = doc[0]
        zoom = _zoom_for(pg.rect.width, pg.rect.height, target_long_px)
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return img.copy()
    finally:
        doc.close()


def render_dxf_to_image(path: str, *,
                        target_long_px: int = DEFAULT_TARGET_LONG_PX) -> Image.Image:
    """Rasterize a DXF modelspace to an RGB PIL image via ezdxf + matplotlib."""
    try:
        import ezdxf
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    except ImportError as e:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "Opening DXF floor plans needs ezdxf + matplotlib.\n"
            "Install them with:  pip install ezdxf matplotlib") from e
    import matplotlib
    matplotlib.use("Agg")  # headless raster backend — no display needed
    import matplotlib.pyplot as plt

    try:
        doc = ezdxf.readfile(path)
    except (OSError, ezdxf.DXFError) as e:
        raise RuntimeError(f"Could not read DXF:\n{e}") from e
    msp = doc.modelspace()

    fig = plt.figure()
    try:
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_axis_off()
        ctx = RenderContext(doc)
        Frontend(ctx, MatplotlibBackend(ax)).draw_layout(msp, finalize=True)
        # Size the figure from the drawing's aspect ratio so the rasterized
        # canvas isn't letterboxed; pick dpi to hit the long-edge budget.
        ax.autoscale(enable=True, tight=True)
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        w_units, h_units = abs(x1 - x0), abs(y1 - y0)
        if w_units <= 0 or h_units <= 0:
            raise RuntimeError("DXF modelspace is empty — nothing to render.")
        aspect = h_units / w_units
        fig_w_in = 10.0
        fig.set_size_inches(fig_w_in, fig_w_in * aspect)
        dpi = max(MIN_ZOOM * 100, min(MAX_ZOOM * 100,
                                      target_long_px / max(fig_w_in, fig_w_in * aspect)))
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    finally:
        plt.close(fig)
    buf.seek(0)
    img = Image.open(buf)
    img.load()
    return img.convert("RGB")


def render_to_image(path: str, *,
                    target_long_px: int = DEFAULT_TARGET_LONG_PX) -> Image.Image:
    """Dispatch by file extension to the matching rasterizer."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return render_pdf_to_image(path, target_long_px=target_long_px)
    if ext == ".svg":
        return render_svg_to_image(path, target_long_px=target_long_px)
    if ext == ".dxf":
        return render_dxf_to_image(path, target_long_px=target_long_px)
    raise RuntimeError(f"Unsupported drawing format: {ext or path!r}")


def image_to_drawio_xml(img: Image.Image, *, diagram_name: str = "Floor") -> str:
    """Wrap a PIL image as a minimal .drawio document whose page size equals
    the image pixel size (so drawio units == image pixels downstream).

    The background cell's style begins with the exact ``shape=image;
    image=data:image/png,`` token that ``parse_drawio`` scans for — keep
    ``image=`` immediately after ``shape=image;`` or the parser won't find
    the background.
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    # base64 contains no ';', so the parser's split-on-';' recovers it cleanly.
    style = (f"shape=image;image=data:image/png,{b64};"
             "verticalLabelPosition=bottom;verticalAlign=top;"
             "aspect=fixed;imageAspect=0;")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<mxfile host="camera-wire-tool">\n'
        f'  <diagram id="floor" name={quoteattr(diagram_name)}>\n'
        f'    <mxGraphModel dx="0" dy="0" grid="0" gridSize="10" '
        f'guides="1" tooltips="1" connect="1" arrows="1" fold="1" '
        f'page="1" pageScale="1" pageWidth="{w}" pageHeight="{h}" '
        f'math="0" shadow="0">\n'
        '      <root>\n'
        '        <mxCell id="0" />\n'
        '        <mxCell id="1" parent="0" />\n'
        f'        <mxCell id="bgimg" style={quoteattr(style)} '
        f'vertex="1" parent="1">\n'
        f'          <mxGeometry x="0" y="0" width="{w}" height="{h}" '
        f'as="geometry" />\n'
        '        </mxCell>\n'
        '      </root>\n'
        '    </mxGraphModel>\n'
        '  </diagram>\n'
        '</mxfile>\n'
    )


def write_drawio_from_drawing(src_path: str, out_path: str, *,
                              diagram_name: str | None = None,
                              target_long_px: int = DEFAULT_TARGET_LONG_PX) -> str:
    """Render `src_path` (PDF/SVG/DXF) and write a .drawio to `out_path`.
    Returns `out_path`."""
    img = render_to_image(src_path, target_long_px=target_long_px)
    name = diagram_name or os.path.splitext(os.path.basename(src_path))[0]
    xml = image_to_drawio_xml(img, diagram_name=name)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(xml)
    os.replace(tmp, out_path)
    return out_path
