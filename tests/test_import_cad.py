"""Tests for cwt.import_cad — DXF/SVG → drawio background conversion.

Each test renders a tiny drawing, converts it, then re-parses the result
with the app's own parse_drawio() to prove the round trip yields a usable
floor plan (embedded background + 1:1 page scale).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import import_cad

# parse_drawio lives in the monolith; importing it pulls in Tk-free helpers.
from camera_wire_tool import parse_drawio

ezdxf = pytest.importorskip("ezdxf")
pytest.importorskip("matplotlib")
fitz = pytest.importorskip("fitz")


SAMPLE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160">
  <rect x="10" y="10" width="220" height="140" fill="none" stroke="black"/>
  <line x1="10" y1="80" x2="230" y2="80" stroke="red"/>
  <text x="20" y="50">Room A</text>
</svg>"""


def _make_dxf(path: str) -> None:
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60), (0, 0)])
    msp.add_line((0, 30), (100, 30))
    msp.add_text("Room B", height=5).set_placement((10, 40))
    doc.saveas(path)


def _assert_valid_plan(drawio_path: str) -> None:
    bg, page_w, page_h, devices, symbols, legend = parse_drawio(drawio_path)
    assert bg is not None, "converted drawio must carry an embedded background"
    # Page dims equal the embedded image size → app's dx/dy scale is 1.0.
    assert (page_w, page_h) == bg.size
    assert page_w > 2 and page_h > 2
    assert devices == []  # an imported plan starts with no devices


def test_svg_round_trip(tmp_path):
    src = tmp_path / "plan.svg"
    src.write_text(SAMPLE_SVG, encoding="utf-8")
    out = import_cad.convert_to_drawio(str(src))
    assert out.endswith(".drawio") and os.path.exists(out)
    _assert_valid_plan(out)


def test_dxf_round_trip(tmp_path):
    src = tmp_path / "plan.dxf"
    _make_dxf(str(src))
    out = import_cad.convert_to_drawio(str(src))
    assert out.endswith(".drawio") and os.path.exists(out)
    _assert_valid_plan(out)


def test_existing_drawio_not_clobbered(tmp_path):
    src = tmp_path / "plan.svg"
    src.write_text(SAMPLE_SVG, encoding="utf-8")
    # Pre-existing sibling .drawio must be preserved, not overwritten.
    existing = tmp_path / "plan.drawio"
    existing.write_text("ORIGINAL", encoding="utf-8")
    out = import_cad.convert_to_drawio(str(src))
    assert os.path.abspath(out) != os.path.abspath(str(existing))
    assert existing.read_text(encoding="utf-8") == "ORIGINAL"


def test_empty_dxf_raises(tmp_path):
    src = tmp_path / "empty.dxf"
    ezdxf.new().saveas(str(src))  # no entities in model space
    with pytest.raises(import_cad.CadImportError):
        import_cad.convert_to_drawio(str(src))


def test_is_cad_drawing():
    assert import_cad.is_cad_drawing("a.dxf")
    assert import_cad.is_cad_drawing("A.SVG")
    assert not import_cad.is_cad_drawing("a.drawio")
    assert not import_cad.is_cad_drawing("a.zsmt")
