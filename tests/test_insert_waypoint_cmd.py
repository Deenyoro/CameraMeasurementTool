"""Tests for InsertWaypointCmd — the undoable insert-vertex command
used by the canvas right-click → 'Insert waypoint here' action."""

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
    # _recalc_route_length resolves the EFFECTIVE scale (project ×
    # floor alignment) + riser inches; the stub has no project, so
    # effective scale == the raw scale_factor and no vertical footage.
    app._effective_scale_factor = lambda: app.scale_factor
    app._route_vertical_in = lambda route: 0.0
    return app


def test_inserts_at_index_and_recomputes_length():
    route = {"points": [(0, 0), (100, 0)], "length_px": 100.0, "length_in": None}
    app = _app(scale_factor=0.5)
    cmd = cwt.InsertWaypointCmd(app, route, 1, (50, 10))
    cmd.do()
    assert route["points"] == [(0, 0), (50, 10), (100, 0)]
    # Length recomputed: hypot(50,10) + hypot(50,10) ≈ 101.98
    assert abs(route["length_px"] - (2 * (50**2 + 10**2)**0.5)) < 1e-6
    # length_in respects scale_factor
    assert abs(route["length_in"] - route["length_px"] * 0.5) < 1e-6


def test_undo_removes_inserted_point():
    route = {"points": [(0, 0), (100, 0)], "length_px": 100.0, "length_in": None}
    app = _app()
    cmd = cwt.InsertWaypointCmd(app, route, 1, (50, 10))
    cmd.do()
    cmd.undo()
    assert route["points"] == [(0, 0), (100, 0)]
    assert route["length_px"] == 100.0


def test_redo_idempotent():
    """Calling do() twice must not insert the same point twice."""
    route = {"points": [(0, 0), (100, 0)], "length_px": 100.0, "length_in": None}
    app = _app()
    cmd = cwt.InsertWaypointCmd(app, route, 1, (50, 10))
    cmd.do()
    cmd.do()   # idempotent
    assert route["points"] == [(0, 0), (50, 10), (100, 0)]


def test_undo_after_external_mutation_doesnt_corrupt():
    """If someone else mutated points between do/undo so that insert_idx
    no longer holds our xy, undo should be a no-op rather than popping
    a random point."""
    route = {"points": [(0, 0), (100, 0)], "length_px": 100.0, "length_in": None}
    app = _app()
    cmd = cwt.InsertWaypointCmd(app, route, 1, (50, 10))
    cmd.do()
    # Some external code clobbered the route
    route["points"] = [(0, 0), (99, 99), (100, 0)]
    cmd.undo()
    # No pop happened (because pts[1] != our xy).
    assert route["points"] == [(0, 0), (99, 99), (100, 0)]


def test_clamps_insert_idx_to_valid_range():
    route = {"points": [(0, 0), (100, 0)], "length_px": 100.0, "length_in": None}
    app = _app()
    # Out-of-range insert idx still works (clamped).
    cmd = cwt.InsertWaypointCmd(app, route, 99, (50, 10))
    cmd.do()
    assert (50, 10) in route["points"]
