"""Wires anchored at a device's center should follow the device when
it moves, but wires whose endpoint was manually nudged elsewhere
should NOT (so the user's manual edits aren't trampled)."""

from __future__ import annotations

import math
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _make_app(scale_factor=1.0):
    """A minimal stand-in with the fields _follow_wires_with_device
    reads. Avoids constructing a Tk root."""
    app = types.SimpleNamespace(
        devices=[
            {"id": "s10", "type": "MD",
             "x": 100.0, "y": 100.0, "w": 30.0, "h": 30.0},
        ],
        wire_routes=[],
        dx_scale=2.0, dy_scale=2.0,   # drawio→image-pixel multiplier
        scale_factor=scale_factor,
        _project=None,
    )
    # Provide a length calculator
    def _calc(pts):
        total = 0.0
        for i in range(len(pts) - 1):
            total += math.hypot(pts[i+1][0] - pts[i][0],
                                 pts[i+1][1] - pts[i][1])
        return total
    app._calc_wire_length = _calc
    # Bind the real _effective_scale_factor so the follow function's
    # length_in update goes through the alignment-aware path (no-op
    # for these tests because _project=None → identity alignment).
    app._effective_scale_factor = lambda: (
        cwt.CameraWireTool._effective_scale_factor(app))
    return app


def test_endpoint_at_center_follows_device():
    app = _make_app()
    # Old center in image coords: (100 + 15) * 2, (100 + 15) * 2 = (230, 230)
    app.wire_routes = [{"device_id": "s10",
                         "points": [(230.0, 230.0), (400.0, 400.0)]}]
    cwt._follow_wires_with_device(app, "s10",
                                    old_x=100.0, old_y=100.0,
                                    new_x=200.0, new_y=200.0)
    # New center: (200 + 15)*2, (200 + 15)*2 = (430, 430)
    assert app.wire_routes[0]["points"][0] == (430.0, 430.0)
    # Last point unchanged (it wasn't at the old center)
    assert app.wire_routes[0]["points"][1] == (400.0, 400.0)


def test_endpoint_far_from_center_does_not_follow():
    app = _make_app()
    # First point is way off (1000, 1000) — manually placed
    app.wire_routes = [{"device_id": "s10",
                         "points": [(1000.0, 1000.0), (1100.0, 1100.0)]}]
    cwt._follow_wires_with_device(app, "s10",
                                    old_x=100.0, old_y=100.0,
                                    new_x=200.0, new_y=200.0)
    assert app.wire_routes[0]["points"][0] == (1000.0, 1000.0)


def test_unrelated_device_wire_untouched():
    app = _make_app()
    app.wire_routes = [{"device_id": "s99",   # not s10
                         "points": [(230.0, 230.0), (400.0, 400.0)]}]
    cwt._follow_wires_with_device(app, "s10",
                                    old_x=100.0, old_y=100.0,
                                    new_x=200.0, new_y=200.0)
    assert app.wire_routes[0]["points"][0] == (230.0, 230.0)


def test_lengths_recomputed_after_follow():
    app = _make_app(scale_factor=0.5)
    app.wire_routes = [{"device_id": "s10",
                         "points": [(230.0, 230.0), (430.0, 230.0)]}]
    cwt._follow_wires_with_device(app, "s10",
                                    old_x=100.0, old_y=100.0,
                                    new_x=200.0, new_y=200.0)
    # New first point: (430, 430), last unchanged: (430, 230)
    # New length_px = hypot(0, 200) = 200
    assert app.wire_routes[0]["length_px"] == 200.0
    assert app.wire_routes[0]["length_in"] == 100.0   # 200 * 0.5


def test_movedevicecmd_do_and_undo_chain_through_wire_follow():
    """End-to-end: MoveDeviceCmd's do/undo should keep wires anchored
    at the device's center in sync, including across redo."""
    app = _make_app()
    dev = app.devices[0]
    app.wire_routes = [{"device_id": "s10",
                         "points": [(230.0, 230.0), (400.0, 400.0)]}]
    cmd = cwt.MoveDeviceCmd(app, dev,
                              old_x=100.0, old_y=100.0,
                              new_x=200.0, new_y=200.0)
    # State pre-do (live drag already moved device — simulate by setting
    # to new and letting do() be the catch-up call for wires).
    dev["x"], dev["y"] = 100.0, 100.0   # back to old
    cmd.do()
    assert dev["x"] == 200.0 and dev["y"] == 200.0
    # First point followed to new center (200+15)*2 = 430
    assert app.wire_routes[0]["points"][0] == (430.0, 430.0)
    cmd.undo()
    assert dev["x"] == 100.0 and dev["y"] == 100.0
    # Wire shifted back to (230, 230)
    assert app.wire_routes[0]["points"][0] == (230.0, 230.0)
    # Redo cycles forward again
    cmd.do()
    assert app.wire_routes[0]["points"][0] == (430.0, 430.0)
