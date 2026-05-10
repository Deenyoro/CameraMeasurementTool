"""SetWireCableTypeCmd round-trips per-route cable_type overrides,
and undo restores the previous value (or absence of key)."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def test_set_cable_type_adds_key():
    route = {"device_id": "s10", "points": [(0, 0), (1, 1)]}
    cmd = cwt.SetWireCableTypeCmd(types.SimpleNamespace(),
                                    [(route, None, "RG59")])
    cmd.do()
    assert route["cable_type"] == "RG59"


def test_set_cable_type_undo_removes_key_when_old_was_none():
    route = {"device_id": "s10", "points": [(0, 0), (1, 1)]}
    cmd = cwt.SetWireCableTypeCmd(types.SimpleNamespace(),
                                    [(route, None, "RG59")])
    cmd.do()
    cmd.undo()
    assert "cable_type" not in route


def test_set_cable_type_undo_restores_previous_value():
    route = {"device_id": "s10", "cable_type": "Cat6",
             "points": [(0, 0), (1, 1)]}
    cmd = cwt.SetWireCableTypeCmd(types.SimpleNamespace(),
                                    [(route, "Cat6", "RG59")])
    cmd.do()
    cmd.undo()
    assert route["cable_type"] == "Cat6"


def test_set_cable_type_to_none_clears_existing():
    route = {"device_id": "s10", "cable_type": "Cat6",
             "points": [(0, 0), (1, 1)]}
    cmd = cwt.SetWireCableTypeCmd(types.SimpleNamespace(),
                                    [(route, "Cat6", None)])
    cmd.do()
    assert "cable_type" not in route
    cmd.undo()
    assert route["cable_type"] == "Cat6"


def test_bulk_set_cable_type_round_trip():
    routes = [
        {"device_id": "s10", "cable_type": "Cat6", "points": [(0, 0), (1, 1)]},
        {"device_id": "s11", "points": [(2, 2), (3, 3)]},   # no cable yet
        {"device_id": "s12", "cable_type": "RG59", "points": [(4, 4), (5, 5)]},
    ]
    entries = [
        (routes[0], "Cat6", "Cat5e"),
        (routes[1], None, "Cat5e"),
        (routes[2], "RG59", "Cat5e"),
    ]
    cmd = cwt.SetWireCableTypeCmd(types.SimpleNamespace(), entries)
    cmd.do()
    assert all(r["cable_type"] == "Cat5e" for r in routes)
    cmd.undo()
    assert routes[0]["cable_type"] == "Cat6"
    assert "cable_type" not in routes[1]
    assert routes[2]["cable_type"] == "RG59"
