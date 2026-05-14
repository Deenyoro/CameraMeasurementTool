"""Tests for DeleteWaypointCmd — the undoable delete-vertex command
used by the canvas right-click → 'Delete this waypoint' action."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _app(scale_factor=None):
    app = types.SimpleNamespace(scale_factor=scale_factor)
    app._calc_wire_length = lambda pts: sum(
        ((pts[i+1][0]-pts[i][0])**2 + (pts[i+1][1]-pts[i][1])**2) ** 0.5
        for i in range(len(pts) - 1)
    )
    return app


def test_removes_interior_point_and_recomputes():
    route = {"points": [(0, 0), (50, 10), (100, 0)],
             "length_px": 100.0, "length_in": None}
    app = _app(scale_factor=0.5)
    cmd = cwt.DeleteWaypointCmd(app, route, 1)
    cmd.do()
    assert route["points"] == [(0, 0), (100, 0)]
    assert abs(route["length_px"] - 100.0) < 1e-6
    assert abs(route["length_in"] - 50.0) < 1e-6


def test_undo_re_inserts_at_original_index():
    route = {"points": [(0, 0), (50, 10), (100, 0)],
             "length_px": 100.0, "length_in": None}
    app = _app()
    cmd = cwt.DeleteWaypointCmd(app, route, 1)
    cmd.do()
    cmd.undo()
    assert route["points"] == [(0, 0), (50, 10), (100, 0)]


def test_redo_idempotent():
    """do() twice in a row must not pop a different point."""
    route = {"points": [(0, 0), (50, 10), (100, 0)],
             "length_px": 100.0, "length_in": None}
    app = _app()
    cmd = cwt.DeleteWaypointCmd(app, route, 1)
    cmd.do()
    cmd.do()
    assert route["points"] == [(0, 0), (100, 0)]


def test_do_on_external_mutation_is_no_op():
    """If points were rearranged so that the snapshotted xy isn't at
    point_idx anymore, do() should leave the route alone."""
    route = {"points": [(0, 0), (50, 10), (100, 0)],
             "length_px": 100.0, "length_in": None}
    app = _app()
    cmd = cwt.DeleteWaypointCmd(app, route, 1)
    # Someone else moved the points
    route["points"] = [(0, 0), (77, 77), (100, 0)]
    cmd.do()
    assert route["points"] == [(0, 0), (77, 77), (100, 0)]


def test_invalid_idx_at_construction_is_safe():
    """Constructing with an out-of-range idx must not crash; do/undo
    become no-ops."""
    route = {"points": [(0, 0), (100, 0)],
             "length_px": 100.0, "length_in": None}
    app = _app()
    cmd = cwt.DeleteWaypointCmd(app, route, 99)
    cmd.do()
    cmd.undo()
    assert route["points"] == [(0, 0), (100, 0)]


def test_undo_idempotent():
    """Undo twice (or undo with the point already re-inserted) should
    not double-insert."""
    route = {"points": [(0, 0), (50, 10), (100, 0)],
             "length_px": 100.0, "length_in": None}
    app = _app()
    cmd = cwt.DeleteWaypointCmd(app, route, 1)
    cmd.do()
    cmd.undo()
    cmd.undo()
    assert route["points"] == [(0, 0), (50, 10), (100, 0)]
