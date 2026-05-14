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
