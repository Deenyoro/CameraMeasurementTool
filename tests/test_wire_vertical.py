"""Tests for cross-floor wire vertical-footage logic: a wire terminating
on a drop point whose home floor differs from the wire's floor accrues the
elevation gap. Covers _vertical_in_for_drop_ids and _route_vertical_in."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf

CWT = cwt.CameraWireTool


def _app(active="F1"):
    f1 = mf.FloorState(id="F1", name="1", elevation_ft=0.0, drawio_file="a")
    f2 = mf.FloorState(id="F2", name="2", elevation_ft=25.0, drawio_file="b")
    p = mf.ProjectState(version=2, active_floor=active, floors=[f1, f2])
    p.drop_points = [
        {"id": "DP1", "x": 0, "y": 0, "floors": ["F1", "F2"], "home_floor": "F2"},
        {"id": "DP2", "x": 0, "y": 0, "floors": ["F1"], "home_floor": "F1"},
    ]
    app = types.SimpleNamespace(_project=p)
    # Bind so _route_vertical_in (which calls self._vertical_in_for_drop_ids)
    # resolves on the stub.
    app._vertical_in_for_drop_ids = lambda ids: CWT._vertical_in_for_drop_ids(app, ids)
    return app


def test_remote_home_adds_gap():
    # active F1, drop home F2 (25 ft) → 300 inches.
    assert CWT._vertical_in_for_drop_ids(_app("F1"), ["DP1"]) == 300.0


def test_home_floor_drop_adds_zero():
    # DP2's home is F1 and we're on F1 → no riser.
    assert CWT._vertical_in_for_drop_ids(_app("F1"), ["DP2"]) == 0.0


def test_wiring_from_home_floor_is_zero():
    # On F2, DP1's home is F2 → wiring there adds nothing.
    assert CWT._vertical_in_for_drop_ids(_app("F2"), ["DP1"]) == 0.0


def test_multiple_drops_sum():
    # DP1 (300) + DP2 (0) → 300.
    assert CWT._vertical_in_for_drop_ids(_app("F1"), ["DP1", "DP2"]) == 300.0


def test_unknown_id_ignored():
    assert CWT._vertical_in_for_drop_ids(_app("F1"), ["NOPE"]) == 0.0


def test_empty_or_none():
    app = _app("F1")
    assert CWT._vertical_in_for_drop_ids(app, []) == 0.0
    assert CWT._vertical_in_for_drop_ids(app, None) == 0.0


def test_no_project():
    app = types.SimpleNamespace(_project=None)
    assert CWT._vertical_in_for_drop_ids(app, ["DP1"]) == 0.0


def test_route_vertical_reads_drop_ids():
    app = _app("F1")
    assert CWT._route_vertical_in(app, {"drop_ids": ["DP1"]}) == 300.0
    assert CWT._route_vertical_in(app, {}) == 0.0
    assert CWT._route_vertical_in(app, "not a dict") == 0.0
