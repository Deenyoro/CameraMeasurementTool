"""Tests for the floor-anchor-pick → alignment-compute wiring in the app.

The user picks 2 anchors on the floor being aligned (target), then 2 on
the reference floor. Previously the code ping-ponged forever without
ever calling compute_alignment. These tests pin the fixed flow.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


class _StubCanvas:
    def __init__(self):
        self.ovals = []

    def create_oval(self, *a, **kw):
        self.ovals.append((a, kw))
        return 1

    def delete(self, *_a, **_k):
        pass

    def focus_set(self):
        pass


class _StubStatus:
    def __init__(self):
        self.last = None

    def config(self, **kw):
        self.last = kw.get("text")


def _stub_app(target, reference, picking_phase=0, points=None):
    """Build a minimal CameraWireTool-like stub for exercising
    `_floor_anchors_picking` branch of _on_left_press. picking_phase 0 =
    just started on target; picking_phase 1 = target done, on reference."""
    app = types.SimpleNamespace()
    app.canvas = _StubCanvas()
    app.status_label = _StubStatus()
    app.mode = cwt.Mode.MOVE
    app._space_pan = False
    app._floor_align_target = target
    if picking_phase == 0:
        app._floor_anchors_picking = list(points or [])
        app._floor_anchor_target = (target, reference)
    else:
        app._floor_anchors_picking = list(points or [])
        app._floor_anchor_target = (reference, target)

    # Minimal coord transforms — identity.
    app._canvas_to_img = lambda x, y: (float(x), float(y))
    app._img_to_canvas = lambda x, y: (float(x), float(y))

    # Track side effects.
    app._dirty_marked = False
    def mark_dirty():
        app._dirty_marked = True
    app._mark_dirty = mark_dirty

    app._switched_to = None
    def switch_floor(fid):
        app._switched_to = fid
    app._switch_floor = switch_floor

    app._redrawn = False
    def redraw():
        app._redrawn = True
    app._redraw_canvas = redraw

    app._flashed = None
    def flash(msg, ttl_ms=None):
        app._flashed = msg
    app._flash_status = flash

    # Sink for the showinfo prompt that fires between phases.
    app._infos = []
    return app


def _patch_messagebox(monkeypatch):
    seen = {"info": 0}
    monkeypatch.setattr(cwt.messagebox, "showinfo",
                         lambda *_a, **_k: seen.__setitem__("info", seen["info"] + 1))
    return seen


def _make_floor(fid):
    return mf.FloorState(id=fid, name=fid, elevation_ft=0.0,
                          drawio_file=f"{fid}.drawio")


def test_first_click_just_updates_status(monkeypatch):
    target = _make_floor("F1")
    reference = _make_floor("F2")
    app = _stub_app(target, reference, picking_phase=0)
    _patch_messagebox(monkeypatch)
    ev = types.SimpleNamespace(x=10.0, y=20.0, state=0)
    cwt.CameraWireTool._on_left_press(app, ev)
    assert app._floor_anchors_picking == [(10.0, 20.0)]
    assert "click point #2/2 on F1" in (app.status_label.last or "")
    # No alignment computed yet.
    assert target.alignment == mf.IDENTITY_ALIGNMENT


def test_second_click_on_target_switches_to_reference(monkeypatch):
    target = _make_floor("F1")
    reference = _make_floor("F2")
    app = _stub_app(target, reference,
                    picking_phase=0, points=[(10.0, 20.0)])
    seen = _patch_messagebox(monkeypatch)
    ev = types.SimpleNamespace(x=200.0, y=20.0, state=0)
    cwt.CameraWireTool._on_left_press(app, ev)
    # Target now has its 2 anchors and we've switched to F2 for the next batch.
    assert target.anchor_points == [[10.0, 20.0], [200.0, 20.0]]
    assert app._switched_to == "F2"
    assert app._floor_anchors_picking == []
    assert app._floor_anchor_target == (reference, target)
    assert seen["info"] == 1
    # Target alignment NOT yet computed (still default).
    assert target.alignment == mf.IDENTITY_ALIGNMENT
    # _floor_align_target preserved across the swap.
    assert app._floor_align_target is target


def test_second_click_on_reference_computes_alignment(monkeypatch):
    target = _make_floor("F1")
    reference = _make_floor("F2")
    # Target already has anchors from phase 0.
    target.anchor_points = [[10.0, 20.0], [200.0, 20.0]]
    app = _stub_app(target, reference,
                    picking_phase=1, points=[(60.0, 70.0)])
    _patch_messagebox(monkeypatch)
    ev = types.SimpleNamespace(x=250.0, y=70.0, state=0)
    cwt.CameraWireTool._on_left_press(app, ev)
    # Reference floor now also has 2 anchors.
    assert reference.anchor_points == [[60.0, 70.0], [250.0, 70.0]]
    # Alignment computed and stored on the TARGET (F1).
    assert target.alignment is not mf.IDENTITY_ALIGNMENT
    assert "tx" in target.alignment and "scale" in target.alignment
    # Pure translation from (10,20)..(200,20) → (60,70)..(250,70):
    # +50 in x, +50 in y, scale 1.
    assert abs(target.alignment["tx"] - 50.0) < 1e-6
    assert abs(target.alignment["ty"] - 50.0) < 1e-6
    assert abs(target.alignment["scale"] - 1.0) < 1e-6
    # State cleared, view returned to the target floor.
    assert app._floor_align_target is None
    assert app._floor_anchors_picking is None
    assert app._switched_to == "F1"
    assert app._dirty_marked is True
    assert app._redrawn is True
    assert "aligned" in (app._flashed or "")
