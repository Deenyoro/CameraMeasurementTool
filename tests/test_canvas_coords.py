"""Tests for the alignment-aware coordinate transforms:
_img_to_canvas / _canvas_to_img. These are the central piece that
makes 'aligned floors share a frame on the main canvas' work, and
their bug surface is large because every device, wire, FOV cone, and
hit-test runs through them."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


def _app(alignment=None, zoom=1.0, offset_x=0.0, offset_y=0.0):
    """Build a stub with the bound coord-transform helpers."""
    proj = None
    if alignment is not None:
        floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                               drawio_file="F1.drawio",
                               alignment=alignment)
        proj = mf.ProjectState(active_floor="F1", floors=[floor])
    app = types.SimpleNamespace(_project=proj, zoom=zoom,
                                  offset_x=offset_x, offset_y=offset_y)
    app._current_alignment = lambda: (
        cwt.CameraWireTool._current_alignment(app))
    app._alignment_scale = lambda: (
        cwt.CameraWireTool._alignment_scale(app))
    return app


def _img_to_canvas(app, ix, iy):
    return cwt.CameraWireTool._img_to_canvas(app, ix, iy)


def _canvas_to_img(app, cx, cy):
    return cwt.CameraWireTool._canvas_to_img(app, cx, cy)


def test_identity_alignment_is_plain_zoom_offset():
    app = _app(alignment=dict(mf.IDENTITY_ALIGNMENT),
                zoom=2.0, offset_x=10, offset_y=20)
    cx, cy = _img_to_canvas(app, 5, 5)
    assert abs(cx - 20) < 1e-9   # 5 * 2 + 10
    assert abs(cy - 30) < 1e-9   # 5 * 2 + 20


def test_canvas_to_img_inverts_img_to_canvas():
    app = _app(alignment={"tx": 50.0, "ty": 30.0, "scale": 1.5,
                            "rotation_deg": 0.0},
                zoom=2.0, offset_x=10, offset_y=20)
    cx, cy = _img_to_canvas(app, 100, 200)
    ix, iy = _canvas_to_img(app, cx, cy)
    assert abs(ix - 100) < 1e-9
    assert abs(iy - 200) < 1e-9


def test_alignment_scale_stretches_canvas_position():
    """A point at raw (100, 100) on a 1.5x-aligned floor lands at
    canvas position aligned*zoom + offset = (150*1 + 0, 150*1 + 0)."""
    app = _app(alignment={"tx": 0, "ty": 0, "scale": 1.5,
                            "rotation_deg": 0},
                zoom=1.0, offset_x=0, offset_y=0)
    cx, cy = _img_to_canvas(app, 100, 100)
    assert abs(cx - 150) < 1e-6
    assert abs(cy - 150) < 1e-6


def test_alignment_translation_offsets_canvas_position():
    """Translation 50, 30 in alignment shifts the point."""
    app = _app(alignment={"tx": 50, "ty": 30, "scale": 1.0,
                            "rotation_deg": 0},
                zoom=1.0, offset_x=0, offset_y=0)
    cx, cy = _img_to_canvas(app, 100, 100)
    # aligned = (100 + 50, 100 + 30) = (150, 130), then * zoom + offset = same.
    assert abs(cx - 150) < 1e-6
    assert abs(cy - 130) < 1e-6


def test_rotation_round_trips():
    """A 30° rotation in alignment.rotation_deg: img→canvas→img recovers."""
    app = _app(alignment={"tx": 0, "ty": 0, "scale": 1.0,
                            "rotation_deg": 30.0},
                zoom=1.5, offset_x=200, offset_y=100)
    for pt in [(0, 0), (50, 100), (-25, 80), (1000, 500)]:
        cx, cy = _img_to_canvas(app, *pt)
        ix, iy = _canvas_to_img(app, cx, cy)
        assert abs(ix - pt[0]) < 1e-6, f"x mismatch for {pt}: got {ix}"
        assert abs(iy - pt[1]) < 1e-6, f"y mismatch for {pt}: got {iy}"


def test_no_project_falls_back_to_identity():
    """Single-floor / pre-project mode: alignment is identity, so
    img_to_canvas reduces to plain zoom+offset."""
    app = _app(alignment=None,
                zoom=2.0, offset_x=10, offset_y=20)
    cx, cy = _img_to_canvas(app, 5, 5)
    assert abs(cx - 20) < 1e-9
    assert abs(cy - 30) < 1e-9


def test_corrupted_alignment_falls_back_to_identity():
    """Non-dict alignment shouldn't crash the transforms."""
    floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                          drawio_file="F1.drawio")
    floor.alignment = None
    proj = mf.ProjectState(active_floor="F1", floors=[floor])
    app = types.SimpleNamespace(_project=proj, zoom=1.0,
                                  offset_x=0, offset_y=0)
    app._current_alignment = lambda: (
        cwt.CameraWireTool._current_alignment(app))
    app._alignment_scale = lambda: (
        cwt.CameraWireTool._alignment_scale(app))
    cx, cy = _img_to_canvas(app, 50, 50)
    assert abs(cx - 50) < 1e-9
    assert abs(cy - 50) < 1e-9
