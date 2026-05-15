"""Tests for CameraWireTool._fit_image — must compute zoom/offset
from the ALIGNED bounding box, not the raw img_w/img_h. Without
this, an aligned floor renders bigger or smaller than _fit expects
and the user sees 'ultra small' / 'half off-canvas' on first open."""

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


def _app(img_w=1000, img_h=800, alignment=None,
          canvas_w=800, canvas_h=600):
    app = types.SimpleNamespace()
    app.canvas = _StubCanvas(canvas_w, canvas_h)
    app.img_w = img_w
    app.img_h = img_h
    app.zoom = 1.0
    app.offset_x = 0.0
    app.offset_y = 0.0
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


def test_identity_alignment_fits_raw_image():
    """1000x800 image, 800x600 canvas → zoom = min(0.8, 0.75) * 0.92."""
    app = _app(img_w=1000, img_h=800)
    cwt.CameraWireTool._fit_image(app)
    # zx = 800/1000 = 0.8, zy = 600/800 = 0.75. min × FIT_MARGIN.
    expected = 0.75 * cwt.C.FIT_MARGIN
    assert abs(app.zoom - expected) < 1e-6


def test_aligned_image_fits_aligned_bbox():
    """A 1000x800 image with alignment.scale = 2.0 effectively covers
    a 2000x1600 aligned bbox — fit should shrink the zoom to fit
    that, not the raw 1000x800."""
    app = _app(img_w=1000, img_h=800,
                alignment={"tx": 0, "ty": 0,
                            "scale": 2.0, "rotation_deg": 0})
    cwt.CameraWireTool._fit_image(app)
    expected = (600 / 1600) * cwt.C.FIT_MARGIN
    assert abs(app.zoom - expected) < 1e-6


def test_aligned_with_translation_centers_bbox():
    """Aligned bbox: 100..1100 in x, 50..850 in y. Center: 600, 450.
    Canvas center: 400, 300. After fit at zoom Z, offset_x should
    place the aligned-bbox center at canvas center."""
    app = _app(img_w=1000, img_h=800, canvas_w=800, canvas_h=600,
                alignment={"tx": 100, "ty": 50,
                            "scale": 1.0, "rotation_deg": 0})
    cwt.CameraWireTool._fit_image(app)
    # Aligned bbox center in aligned-space: (600, 450).
    # Canvas: (400, 300). After fit, the bbox center should land at canvas center.
    canvas_center_x = 800 / 2
    canvas_center_y = 600 / 2
    bbox_center_aligned_x = 600  # (100 + 1100) / 2
    bbox_center_aligned_y = 450  # (50 + 850) / 2
    actual_canvas_x = bbox_center_aligned_x * app.zoom + app.offset_x
    actual_canvas_y = bbox_center_aligned_y * app.zoom + app.offset_y
    assert abs(actual_canvas_x - canvas_center_x) < 1.0
    assert abs(actual_canvas_y - canvas_center_y) < 1.0


def test_no_project_falls_back_to_raw():
    """Pre-project / single-floor state: fit uses raw img dimensions."""
    app = _app(img_w=1000, img_h=800)
    app._project = None
    cwt.CameraWireTool._fit_image(app)
    expected = (600 / 800) * cwt.C.FIT_MARGIN
    assert abs(app.zoom - expected) < 1e-6
