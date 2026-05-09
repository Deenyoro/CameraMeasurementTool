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
    )
    # Provide a length calculator
    def _calc(pts):
        total = 0.0
        for i in range(len(pts) - 1):
            total += math.hypot(pts[i+1][0] - pts[i][0],
                                 pts[i+1][1] - pts[i][1])
        return total
    app._calc_wire_length = _calc
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
