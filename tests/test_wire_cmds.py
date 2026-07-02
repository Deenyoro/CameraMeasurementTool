"""Wire-route command round-trips: AddWireCmd, DeleteWireCmd,
ClearWiresCmd, SimplifyWireCmd, MoveWaypointCmd. These are
foundational — every wire-drawing path goes through them."""

from __future__ import annotations

import math
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _make_app(scale_factor=None):
    app = types.SimpleNamespace(wire_routes=[], scale_factor=scale_factor)
    def _calc(pts):
        return sum(math.hypot(b[0]-a[0], b[1]-a[1])
                    for a, b in zip(pts, pts[1:], strict=False))
    app._calc_wire_length = _calc
    # _recalc_route_length resolves the EFFECTIVE scale (project ×
    # floor alignment) + riser inches; the stub has no project, so
    # effective scale == the raw scale_factor and no vertical footage.
    app._effective_scale_factor = lambda: app.scale_factor
    app._route_vertical_in = lambda route: 0.0
    return app


# ---------------- AddWireCmd
def test_add_wire_appends_and_undo_removes():
    app = _make_app()
    route = {"device_id": "s10", "points": [(0, 0), (10, 10)]}
    cmd = cwt.AddWireCmd(app, route)
    cmd.do()
    assert app.wire_routes == [route]
    cmd.undo()
    assert app.wire_routes == []


def test_add_wire_idempotent():
    app = _make_app()
    route = {"device_id": "s10", "points": [(0, 0), (10, 10)]}
    cmd = cwt.AddWireCmd(app, route)
    cmd.do(); cmd.do()
    assert len(app.wire_routes) == 1


# ---------------- DeleteWireCmd
def test_delete_wire_at_index_and_undo_restores():
    app = _make_app()
    a = {"device_id": "s10", "points": [(0, 0), (1, 1)]}
    b = {"device_id": "s11", "points": [(2, 2), (3, 3)]}
    c = {"device_id": "s12", "points": [(4, 4), (5, 5)]}
    app.wire_routes = [a, b, c]
    cmd = cwt.DeleteWireCmd(app, index=1, route=b)
    cmd.do()
    assert app.wire_routes == [a, c]
    cmd.undo()
    assert app.wire_routes == [a, b, c]


# ---------------- ClearWiresCmd
def test_clear_wires_undo_restores_snapshot():
    app = _make_app()
    a = {"device_id": "s10", "points": [(0, 0), (1, 1)]}
    b = {"device_id": "s11", "points": [(2, 2), (3, 3)]}
    app.wire_routes = [a, b]
    cmd = cwt.ClearWiresCmd(app, app.wire_routes)
    cmd.do()
    assert app.wire_routes == []
    cmd.undo()
    assert app.wire_routes == [a, b]


# ---------------- SimplifyWireCmd
def test_simplify_wire_replaces_points_and_recomputes_length():
    app = _make_app(scale_factor=2.0)
    route = {"device_id": "s10",
             "points": [(0, 0), (5, 0), (10, 0)],   # 3 colinear points
             "length_px": 10, "length_in": 20}
    app.wire_routes = [route]
    new_pts = [(0, 0), (10, 0)]
    cmd = cwt.SimplifyWireCmd(app, route, new_pts)
    cmd.do()
    assert route["points"] == new_pts
    assert route["length_px"] == 10
    assert route["length_in"] == 20
    cmd.undo()
    assert route["points"] == [(0, 0), (5, 0), (10, 0)]


# ---------------- MoveWaypointCmd
def test_move_waypoint_round_trip():
    app = _make_app()
    route = {"device_id": "s10", "points": [(0, 0), (5, 0), (10, 0)],
             "length_px": 10, "length_in": None}
    app.wire_routes = [route]
    cmd = cwt.MoveWaypointCmd(app, route, point_idx=1,
                                old_xy=(5, 0), new_xy=(5, 5))
    cmd.do()
    assert route["points"][1] == (5, 5)
    # New length: hypot(5,5) + hypot(5,5) ≈ 14.142
    assert abs(route["length_px"] - 14.142) < 0.01
    cmd.undo()
    assert route["points"][1] == (5, 0)
    assert route["length_px"] == 10


def test_move_waypoint_index_out_of_range_is_noop():
    app = _make_app()
    route = {"device_id": "s10", "points": [(0, 0), (10, 0)]}
    app.wire_routes = [route]
    cmd = cwt.MoveWaypointCmd(app, route, point_idx=99,
                                old_xy=(0, 0), new_xy=(99, 99))
    cmd.do()   # silently no-op since index is out of range
    assert route["points"] == [(0, 0), (10, 0)]
