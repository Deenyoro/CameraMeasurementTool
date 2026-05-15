"""Tests for CameraWireTool._wire_at — the canvas click-to-pick-a-wire
hit test. Runs on every canvas click, so it has to skip corrupted
routes silently rather than crash."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub(routes):
    app = types.SimpleNamespace(wire_routes=routes, zoom=1.0)
    app._point_to_segment_dist = staticmethod(
        cwt.CameraWireTool._point_to_segment_dist)
    return app


def test_picks_route_along_segment():
    routes = [{"points": [(0, 0), (100, 0)]}]
    app = _stub(routes)
    # Point right on the segment
    assert cwt.CameraWireTool._wire_at(app, 50, 0) == 0


def test_no_hit_far_from_routes():
    routes = [{"points": [(0, 0), (100, 0)]}]
    app = _stub(routes)
    assert cwt.CameraWireTool._wire_at(app, 50, 500) is None


def test_skips_non_dict_route():
    """A stray string in wire_routes used to crash on route['points']."""
    routes = ["not a route", {"points": [(0, 0), (100, 0)]}]
    app = _stub(routes)
    assert cwt.CameraWireTool._wire_at(app, 50, 0) == 1


def test_skips_route_missing_points():
    routes = [{"device_id": "s1"},   # no points
              {"points": [(0, 0), (100, 0)]}]
    app = _stub(routes)
    assert cwt.CameraWireTool._wire_at(app, 50, 0) == 1


def test_skips_route_with_non_list_points():
    routes = [{"points": "garbage"},
              {"points": [(0, 0), (100, 0)]}]
    app = _stub(routes)
    assert cwt.CameraWireTool._wire_at(app, 50, 0) == 1


def test_skips_route_with_single_point():
    """A 1-point 'route' has no segment — must not crash on pts[j+1]."""
    routes = [{"points": [(0, 0)]},
              {"points": [(0, 0), (100, 0)]}]
    app = _stub(routes)
    assert cwt.CameraWireTool._wire_at(app, 50, 0) == 1


def test_empty_routes_returns_none():
    app = _stub([])
    assert cwt.CameraWireTool._wire_at(app, 0, 0) is None


def test_threshold_shrinks_with_higher_alignment_scale(monkeypatch):
    """At alignment.scale = 2.0, each raw pixel is 2 canvas pixels wide,
    so the 6-canvas-px grab radius is only ~3 raw pixels — a click at
    raw y=5 should miss a horizontal wire at y=0."""
    from cwt import multifloor as mf
    routes = [{"points": [(0, 0), (100, 0)]}]
    app = _stub(routes)
    floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                           drawio_file="F1.drawio",
                           alignment={"tx": 0, "ty": 0,
                                       "scale": 2.0, "rotation_deg": 0})
    app._project = mf.ProjectState(active_floor="F1", floors=[floor])
    app._current_alignment = lambda: (
        cwt.CameraWireTool._current_alignment(app))
    # 4 raw px ≈ 8 canvas px away from the wire — at zoom 1, alignment 2,
    # the threshold is max(4, 6/(1*2)) = 4 raw px. So a click 4 raw px
    # away should be on the edge (or just inside) and hit, but 6 raw px
    # should miss.
    assert cwt.CameraWireTool._wire_at(app, 50, 6) is None
    # A direct hit still works.
    assert cwt.CameraWireTool._wire_at(app, 50, 0) == 0


def test_threshold_grows_with_lower_alignment_scale():
    """At alignment.scale = 0.5, each raw pixel is half a canvas pixel,
    so the grab radius needs to extend further in raw space to stay at
    6 canvas pixels."""
    from cwt import multifloor as mf
    routes = [{"points": [(0, 0), (100, 0)]}]
    app = _stub(routes)
    floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                           drawio_file="F1.drawio",
                           alignment={"tx": 0, "ty": 0,
                                       "scale": 0.5, "rotation_deg": 0})
    app._project = mf.ProjectState(active_floor="F1", floors=[floor])
    app._current_alignment = lambda: (
        cwt.CameraWireTool._current_alignment(app))
    # At zoom 1, alignment 0.5, threshold = max(4, 6/(0.5)) = 12 raw px.
    # A click 10 raw px from the wire should hit; 14 should miss.
    assert cwt.CameraWireTool._wire_at(app, 50, 10) == 0
    assert cwt.CameraWireTool._wire_at(app, 50, 14) is None
