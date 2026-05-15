"""Tests for CameraWireTool._waypoint_at — the on-press hit-test that
arms a waypoint drag. Endpoints have a wider grab radius than interior
points (they sit on top of devices). Threshold scales with
zoom × alignment.scale so the felt-grab radius is constant in canvas
pixels across aligned floors."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


def _stub(route, zoom=1.0, alignment=None, wires_visible=True):
    """Build a stub with the selected-wire fixture and alignment ctx."""
    app = types.SimpleNamespace(zoom=zoom)
    # _waypoint_at calls _selected_wire on self; pin it to the fixture.
    app._selected_wire = lambda: route
    # _show_wires bound var (real Tk would be a BooleanVar)
    show_var = types.SimpleNamespace(get=lambda v=wires_visible: v)
    app._show_wires = show_var
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


def test_hits_start_endpoint():
    route = {"points": [(100.0, 100.0), (200.0, 100.0), (300.0, 100.0)]}
    app = _stub(route)
    hit = cwt.CameraWireTool._waypoint_at(app, 100, 100)
    assert hit is not None
    assert hit[0] is route
    assert hit[1] == 0


def test_hits_end_endpoint():
    route = {"points": [(0.0, 0.0), (50.0, 50.0), (300.0, 100.0)]}
    app = _stub(route)
    hit = cwt.CameraWireTool._waypoint_at(app, 300, 100)
    assert hit is not None
    assert hit[1] == 2   # last index


def test_hits_interior_waypoint():
    route = {"points": [(0.0, 0.0), (50.0, 50.0), (300.0, 100.0)]}
    app = _stub(route)
    hit = cwt.CameraWireTool._waypoint_at(app, 50, 50)
    assert hit is not None
    assert hit[1] == 1


def test_misses_when_far_from_any_waypoint():
    route = {"points": [(0.0, 0.0), (300.0, 100.0)]}
    app = _stub(route)
    assert cwt.CameraWireTool._waypoint_at(app, 1000, 1000) is None


def test_endpoint_threshold_larger_than_interior():
    """Endpoints (0, last) have a 16-canvas-px grab radius vs 12 for
    interior. A click 14 canvas px from the END should hit, same
    distance from a MIDDLE point should miss."""
    # At zoom=1, alignment identity: end_thresh ≈ 16, mid_thresh ≈ 12.
    route = {"points": [(0.0, 0.0), (200.0, 0.0), (400.0, 0.0)]}
    app = _stub(route)
    # 14 raw px above the END (idx 2): hit (end_thresh 16)
    assert cwt.CameraWireTool._waypoint_at(app, 400, 14) is not None
    # 14 raw px above the MID (idx 1): miss (mid_thresh 12)
    assert cwt.CameraWireTool._waypoint_at(app, 200, 14) is None


def test_returns_none_when_no_wire_selected():
    app = types.SimpleNamespace(zoom=1.0)
    app._selected_wire = lambda: None
    show_var = types.SimpleNamespace(get=lambda: True)
    app._show_wires = show_var
    app._project = None
    app._current_alignment = lambda: (
        cwt.CameraWireTool._current_alignment(app))
    app._alignment_scale = lambda: (
        cwt.CameraWireTool._alignment_scale(app))
    assert cwt.CameraWireTool._waypoint_at(app, 50, 50) is None


def test_returns_none_when_wires_hidden():
    route = {"points": [(100.0, 100.0), (200.0, 100.0)]}
    app = _stub(route, wires_visible=False)
    # Even a direct hit on the waypoint shouldn't fire while wires are hidden.
    assert cwt.CameraWireTool._waypoint_at(app, 100, 100) is None


def test_threshold_shrinks_with_alignment_scale():
    """At alignment.scale = 2.0, the raw-pixel threshold halves. A 14px
    raw click from a mid waypoint that used to hit (mid_thresh 12 raw at
    scale 1) should now miss (mid_thresh 6 raw at scale 2)."""
    route = {"points": [(0.0, 0.0), (200.0, 0.0), (400.0, 0.0)]}
    app = _stub(route, alignment={"tx": 0, "ty": 0,
                                     "scale": 2.0, "rotation_deg": 0})
    # Mid threshold at scale 2: max(6, 12/2) = 6. 8 raw px away should miss.
    assert cwt.CameraWireTool._waypoint_at(app, 200, 8) is None
    # And a direct hit still works.
    assert cwt.CameraWireTool._waypoint_at(app, 200, 0) is not None


def test_threshold_grows_with_low_alignment_scale():
    """At alignment.scale = 0.5, raw-pixel threshold doubles. A 20px
    click from a mid waypoint that used to miss (mid_thresh 12 at scale 1)
    should now hit (mid_thresh 24 at scale 0.5)."""
    route = {"points": [(0.0, 0.0), (200.0, 0.0), (400.0, 0.0)]}
    app = _stub(route, alignment={"tx": 0, "ty": 0,
                                     "scale": 0.5, "rotation_deg": 0})
    # mid_thresh = max(6, 12/0.5) = 24. A 20-px-away click should now hit.
    assert cwt.CameraWireTool._waypoint_at(app, 200, 20) is not None


def test_returns_nearest_when_two_points_within_threshold():
    """When two waypoints are both inside the threshold (e.g. very short
    wire), the closer one should win."""
    route = {"points": [(100.0, 100.0), (105.0, 105.0), (200.0, 200.0)]}
    app = _stub(route)
    # Click between point 0 and 1, slightly closer to 0.
    hit = cwt.CameraWireTool._waypoint_at(app, 101, 101)
    assert hit is not None
    assert hit[1] == 0
