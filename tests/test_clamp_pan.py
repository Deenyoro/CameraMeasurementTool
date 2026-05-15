"""Tests for CameraWireTool._clamp_pan — keeps at least ~10% of the
image visible on canvas. Alignment-aware: aligned floors should
clamp against their displayed bbox, not their raw img dimensions."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


class _StubCanvas:
    def __init__(self, w=800, h=600):
        self._w, self._h = w, h

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h


def _app(img_w, img_h, zoom, offset_x, offset_y, alignment=None):
    app = types.SimpleNamespace()
    app.canvas = _StubCanvas()
    app.img_w = img_w
    app.img_h = img_h
    app.zoom = zoom
    app.offset_x = offset_x
    app.offset_y = offset_y
    if alignment is not None:
        floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                               drawio_file="F1.drawio",
                               alignment=alignment)
        app._project = mf.ProjectState(active_floor="F1", floors=[floor])
    else:
        app._project = None
    app._current_alignment = lambda: (
        cwt.CameraWireTool._current_alignment(app))
    app._alignment_scale = lambda: (
        cwt.CameraWireTool._alignment_scale(app))
    return app


def test_clamp_keeps_image_visible_at_extreme_offset():
    """With offset way past the right edge, clamp should pull it back."""
    app = _app(img_w=1000, img_h=800, zoom=1.0,
                offset_x=2000, offset_y=0)
    cwt.CameraWireTool._clamp_pan(app)
    # 10% margin = 80 px; left edge ≤ cw - margin = 720.
    assert app.offset_x <= 720 + 1e-6


def test_clamp_keeps_image_visible_at_negative_offset():
    """Image fully off the left → clamp pulls right edge into margin."""
    app = _app(img_w=1000, img_h=800, zoom=1.0,
                offset_x=-2000, offset_y=0)
    cwt.CameraWireTool._clamp_pan(app)
    # right edge = offset_x + img_w*zoom; need ≥ margin.
    right_edge = app.offset_x + 1000 * app.zoom
    assert right_edge >= 80 - 1e-6


def test_clamp_no_op_when_image_within_canvas():
    """If the image is well within view, offsets shouldn't change."""
    app = _app(img_w=400, img_h=300, zoom=1.0,
                offset_x=100, offset_y=100)
    cwt.CameraWireTool._clamp_pan(app)
    assert abs(app.offset_x - 100) < 1e-6
    assert abs(app.offset_y - 100) < 1e-6


def test_clamp_respects_alignment_scale():
    """A 2x-aligned floor effectively covers 2 * img_w on canvas at
    zoom=1, so the right-edge-in clamp limit is further right than
    the raw img_w would suggest."""
    app = _app(img_w=400, img_h=300, zoom=1.0,
                offset_x=0, offset_y=0,
                alignment={"tx": 0, "ty": 0, "scale": 2.0,
                            "rotation_deg": 0})
    cwt.CameraWireTool._clamp_pan(app)
    # Aligned bbox: 0..800 in x. cw=800, margin=80. Limit: offset_x ≤
    # cw - margin - ax0*zoom = 800 - 80 - 0 = 720. offset 0 within limit.
    assert abs(app.offset_x - 0) < 1e-6


def test_clamp_with_alignment_translation():
    """Alignment.tx shifts the bbox — clamp uses the shifted bbox."""
    app = _app(img_w=400, img_h=300, zoom=1.0,
                offset_x=0, offset_y=0,
                alignment={"tx": 100, "ty": 0, "scale": 1.0,
                            "rotation_deg": 0})
    cwt.CameraWireTool._clamp_pan(app)
    # Aligned bbox: 100..500 in x. With offset 0, left edge on canvas =
    # 0 + 100*1 = 100. Right edge = 500. Both visible. No clamp.
    assert abs(app.offset_x - 0) < 1e-6


def test_clamp_with_no_project_uses_raw_dimensions():
    """Pre-project state: alignment is identity, clamp uses raw dims."""
    app = _app(img_w=1000, img_h=800, zoom=1.0,
                offset_x=2000, offset_y=0)
    # no project → identity alignment
    cwt.CameraWireTool._clamp_pan(app)
    assert app.offset_x <= 720 + 1e-6
