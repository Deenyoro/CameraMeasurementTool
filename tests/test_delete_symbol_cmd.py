"""DeleteSymbolCmd must cascade: remove the type, every device of that
type, AND those devices' wires — with full undo restoration."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _make_app_stub():
    md_info = {"label": "Minidome", "shape": "ellipse",
               "color": "#FFFF00", "is_camera": True}
    return types.SimpleNamespace(
        symbol_types={
            "MD": md_info,
            "CR": {"label": "Card Reader", "shape": "rect",
                   "color": "#00E080", "is_camera": False},
        },
        devices=[
            {"id": "s10", "type": "MD"},
            {"id": "s11", "type": "MD"},
            {"id": "s20", "type": "CR"},
        ],
        wire_routes=[
            {"device_id": "s10", "points": [(0, 0), (1, 1)]},
            {"device_id": "s11", "points": [(2, 2), (3, 3)]},
            {"device_id": "s20", "points": [(4, 4), (5, 5)]},
        ],
    )


def test_delete_symbol_cascades_devices_and_wires():
    app = _make_app_stub()
    md_info = app.symbol_types["MD"]
    md_devices = [d for d in app.devices if d["type"] == "MD"]
    cmd = cwt.DeleteSymbolCmd(app, "MD", md_info, md_devices)
    cmd.do()

    assert "MD" not in app.symbol_types
    # Both MD devices gone
    assert all(d["type"] != "MD" for d in app.devices)
    # CR device + its wire untouched
    assert any(d["id"] == "s20" for d in app.devices)
    assert len(app.wire_routes) == 1
    assert app.wire_routes[0]["device_id"] == "s20"


def test_undo_delete_symbol_restores_everything():
    app = _make_app_stub()
    md_info = app.symbol_types["MD"]
    md_devices = [d for d in app.devices if d["type"] == "MD"]
    original_devices = list(app.devices)
    original_wires = list(app.wire_routes)
    cmd = cwt.DeleteSymbolCmd(app, "MD", md_info, md_devices)
    cmd.do()
    cmd.undo()

    assert "MD" in app.symbol_types
    # Devices restored (set comparison — order isn't strictly preserved
    # because DeleteSymbolCmd appends them back via extend)
    assert {d["id"] for d in app.devices} == {d["id"] for d in original_devices}
    # Every wire is back
    assert len(app.wire_routes) == len(original_wires)
    for restored, original in zip(app.wire_routes, original_wires):
        assert restored is original
