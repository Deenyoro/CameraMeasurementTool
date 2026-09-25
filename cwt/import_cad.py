"""Import vector drawings (DXF, SVG) as drawio floor plans.

The rest of the app is built around a ``.drawio`` document whose floor
plan is a PNG embedded in an ``<mxCell shape=image>``. To let users open
DXF and SVG files we rasterize the drawing to a PNG and wrap it in a
minimal drawio document — downstream everything (device placement, scale
calibration, save, PDF/CSV export) then works unchanged, with no special
cases anywhere else in the app.

The heavy rendering backends are optional and imported lazily:

* DXF  → ``ezdxf`` + ``matplotlib`` (Agg)
* SVG  → ``PyMuPDF`` (``fitz``)

A missing backend raises :class:`CadImportError` that says, in plain
language, what to do instead (and, when running from source, which
package to install), so the base app still runs without them.
:func:`available_exts` tells the UI up front which formats this copy of
the app can open, so it only offers those.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import os
import sys
import xml.etree.ElementTree as ET

from PIL import Image

# Source extensions we can convert into a drawio background.
SUPPORTED_CAD_EXTS = (".dxf", ".svg")

# Target longest side (px) for the rasterized plan. ~3000px matches the
# existing sample plans the app ships with and keeps canvas redraw snappy.
_TARGET_LONG_PX = 3000
# Hard ceiling so a pathological drawing can't produce a multi-hundred-MB
# bitmap that blows up memory and the embedded-base64 file size.
_MAX_LONG_PX = 6000


# The modules each format needs. The Windows .exe is built without them
# (they would add well over 100 MB), so there they are simply absent.
_BACKENDS = {
    ".dxf": ("ezdxf", "matplotlib"),
    ".svg": ("fitz",),
}
_PIP_NAMES = {".dxf": "ezdxf matplotlib", ".svg": "pymupdf"}


class CadImportError(RuntimeError):
    """A DXF/SVG could not be rendered into a drawio background."""


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def available_exts() -> tuple[str, ...]:
    """The drawing extensions this copy of the app can open (a subset of
    :data:`SUPPORTED_CAD_EXTS`). Only checks that the backends can be
    found; nothing heavy is imported."""
    return tuple(ext for ext in SUPPORTED_CAD_EXTS
                 if all(_module_present(m) for m in _BACKENDS[ext]))


def missing_backend_message(ext: str) -> str:
    """Why a .dxf/.svg can't be opened here and what to do instead, for
    someone who has never used a command line. The pip hint is only
    given when running from source, where it can actually be followed."""
    ext = ext.lower()
    kind = ext.lstrip(".").upper()
    msg = (f"This copy of Camera Wire Tool can't open {kind} drawings.\n\n"
           "What you can do instead:\n"
           "  \u2022  Ask whoever sent the plan for a .drawio file or a "
           "Camera Wire Tool project (.zsmt).\n"
           f"  \u2022  Or open the {kind} in your CAD program, export the "
           "plan as a PNG image, place that image in draw.io "
           "(File > Import), save it as .drawio, and open that here.")
    if not getattr(sys, "frozen", False) and ext in _PIP_NAMES:
        msg += ("\n\nRunning from source? Install the importer with:\n"
                f"    pip install {_PIP_NAMES[ext]}")
    return msg


def is_cad_drawing(path: str) -> bool:
    """True if `path` is a vector drawing we can import (by extension)."""
    return path.lower().endswith(SUPPORTED_CAD_EXTS)


# ---------------------------------------------------------------- raster helpers
def _flatten_to_white(img: Image.Image) -> Image.Image:
    """Composite any transparency onto white and return an RGB image.

    Floor plans are line-work on a transparent or white ground; pasting
    onto white gives the same look draw.io would show and avoids a black
    background where the canvas later composites the PNG."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def _cap_size(img: Image.Image) -> Image.Image:
    """Downscale so the longest side is at most ``_MAX_LONG_PX``."""
    long_side = max(img.size)
    if long_side > _MAX_LONG_PX:
        s = _MAX_LONG_PX / long_side
        img = img.resize(
            (max(1, round(img.width * s)), max(1, round(img.height * s))),
            Image.LANCZOS,
        )
    return img


# ---------------------------------------------------------------- SVG → image
def render_svg(path: str) -> Image.Image:
    """Rasterize an SVG to an RGB :class:`PIL.Image.Image`."""
    try:
        import fitz  # PyMuPDF  # noqa: PLC0415 (optional backend, lazy by design)
    except ImportError as e:
        raise CadImportError(missing_backend_message(".svg")) from e

    try:
        doc = fitz.open(path)
    except Exception as e:  # fitz raises a variety of types on bad input
        raise CadImportError(f"Could not read SVG:\n{e}") from e

    try:
        if doc.page_count == 0:
            raise CadImportError("SVG has no drawable page.")
        # Convert to PDF first — the most reliable rasterization path for
        # SVG across PyMuPDF versions (a direct get_pixmap on the SVG page
        # is flaky on some builds).
        pdf = fitz.open("pdf", doc.convert_to_pdf())
        try:
            page = pdf[0]
            long_side = max(page.rect.width, page.rect.height) or 1.0
            scale = _TARGET_LONG_PX / long_side
            scale = max(0.1, min(scale, 4.0))  # don't over-upscale tiny art
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            pdf.close()
    finally:
        doc.close()

    if img.width < 2 or img.height < 2:
        raise CadImportError("SVG rendered to an empty image.")
    return _cap_size(_flatten_to_white(img))


# ---------------------------------------------------------------- DXF → image
def render_dxf(path: str) -> Image.Image:
    """Rasterize a DXF model space to an RGB :class:`PIL.Image.Image`."""
    try:
        import ezdxf  # noqa: PLC0415 (optional backend, lazy by design)
        from ezdxf import bbox  # noqa: PLC0415
        from ezdxf.addons.drawing import Frontend, RenderContext  # noqa: PLC0415
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: PLC0415
    except ImportError as e:
        raise CadImportError(missing_backend_message(".dxf")) from e

    # A bare Figure on its own Agg canvas, not pyplot: pyplot keeps
    # global state that is not thread-safe, and the app renders on a
    # worker thread. It also never touches the app's Tk loop. (ezdxf
    # imports pyplot itself, so still keep that on the headless backend.)
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: PLC0415
    from matplotlib.figure import Figure  # noqa: PLC0415

    try:
        doc = ezdxf.readfile(path)
    except (OSError, ezdxf.DXFError) as e:
        raise CadImportError(f"Could not read DXF:\n{e}") from e

    msp = doc.modelspace()
    try:
        ext = bbox.extents(msp)
    except Exception:
        ext = None
    if ext is None or not ext.has_data or ext.size.x <= 0 or ext.size.y <= 0:
        raise CadImportError(
            "DXF has no drawable geometry in model space "
            "(empty drawing, or geometry only in paper-space layouts)."
        )

    aspect = ext.size.y / ext.size.x
    dpi = 100
    fig_w_in = _TARGET_LONG_PX / dpi
    fig_h_in = max(0.5, min(fig_w_in * aspect, _MAX_LONG_PX / dpi))

    fig = Figure(figsize=(fig_w_in, fig_h_in), dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_axis_off()
    try:
        ctx = RenderContext(doc)
        Frontend(ctx, MatplotlibBackend(ax)).draw_layout(msp, finalize=True)
        buf = io.BytesIO()
        fig.savefig(buf, dpi=dpi, facecolor="white")
    except Exception as e:
        raise CadImportError(f"Could not render DXF:\n{e}") from e

    buf.seek(0)
    img = Image.open(buf)
    img.load()
    return _cap_size(_flatten_to_white(img))


# ---------------------------------------------------------------- image → drawio
def image_to_drawio_xml(img: Image.Image, *, diagram_name: str = "Floor") -> str:
    """Wrap a raster floor plan in a minimal drawio document.

    Page dimensions are set to the image's pixel size so the app's
    drawio-unit ↔ image-pixel scale is exactly 1.0 — devices the user
    places land where they clicked, with no extra calibration math."""
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    w, h = rgb.size

    mxfile = ET.Element("mxfile", {"host": "camera-wire-tool"})
    diagram = ET.SubElement(mxfile, "diagram", {"name": diagram_name})
    model = ET.SubElement(
        diagram,
        "mxGraphModel",
        {
            "dx": "0", "dy": "0", "grid": "0", "gridSize": "10",
            "guides": "1", "tooltips": "1", "connect": "1", "arrows": "1",
            "fold": "1", "page": "1", "pageScale": "1",
            "pageWidth": str(w), "pageHeight": str(h),
            "math": "0", "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    # Background image cell. The exact `shape=image;image=data:image/png,`
    # prefix is the literal substring parse_drawio() keys on to locate the
    # floor plan — keep `image=` immediately after `shape=image;`.
    bg = ET.SubElement(
        root,
        "mxCell",
        {
            "id": "bgimg",
            "style": (
                f"shape=image;image=data:image/png,{b64};"
                "verticalAlign=top;aspect=fixed;imageAspect=0;"
            ),
            "vertex": "1",
            "parent": "1",
        },
    )
    ET.SubElement(
        bg,
        "mxGeometry",
        {"x": "0", "y": "0", "width": str(w), "height": str(h), "as": "geometry"},
    )
    return ET.tostring(mxfile, encoding="unicode")


def render_cad(path: str) -> Image.Image:
    """Rasterize a supported drawing (.dxf/.svg) to an RGB image."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".svg":
        return render_svg(path)
    if ext == ".dxf":
        return render_dxf(path)
    raise CadImportError(f"Unsupported drawing type: {ext or path!r}")


def _unique_path(path: str) -> str:
    """`path`, or `path (1)`/`path (2)`… if it already exists — so an
    earlier import isn't silently clobbered."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def render_to_drawio_xml(path: str) -> str:
    """Render a DXF/SVG to the XML of a PNG-backed ``.drawio`` without
    writing anything, so a cancelled import leaves no file behind. This
    is the slow part; run it off the UI thread."""
    img = render_cad(path)
    name = os.path.splitext(os.path.basename(path))[0]
    return image_to_drawio_xml(img, diagram_name=name or "Floor")


def write_drawio_xml(path: str, xml: str, out_path: str | None = None) -> str:
    """Write `xml` (from :func:`render_to_drawio_xml`) next to the source
    drawing `path` and return the new file's path. The name is uniquified
    so an earlier import isn't overwritten."""
    if out_path is None:
        out_path = _unique_path(os.path.splitext(path)[0] + ".drawio")

    tmp = out_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write(xml)
        os.replace(tmp, out_path)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise CadImportError(f"Could not write converted drawing:\n{e}") from e
    return out_path


def convert_to_drawio(path: str, out_path: str | None = None) -> str:
    """Render a DXF/SVG to a PNG-backed ``.drawio`` and return its path.

    The drawio is written next to the source (uniquified so an existing
    file isn't overwritten) because the app re-reads it from disk on every
    Save."""
    return write_drawio_xml(path, render_to_drawio_xml(path), out_path)
