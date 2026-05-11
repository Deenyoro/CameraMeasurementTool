"""Defensive tests for _apply_autosave's corruption-tolerance.

Tk-instantiating the app for these would be heavy; instead we exercise
the apply path against a stubbed-out app whose attributes match what
_apply_autosave reads/writes."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub_app():
    """Minimal app stand-in. Provides the attributes _apply_autosave
    reads and the no-op refresh methods it calls at the end."""
    app = types.SimpleNamespace(
        scale_factor=None,
        symbol_types={"MD": {"label": "Mini", "shape": "ellipse",
                              "color": "#FFFF00", "is_camera": True}},
        cable_types={"Cat6": {"label": "Cat6 PoE", "color": "#0080FF",
                                "cost_per_ft": 0.18}},
        devices=[],
        wire_routes=[],
        annotations=[],
        legend_box={"x": 0, "y": 0, "w": 200, "title": "Legend"},
        _next_device_id=100,
    )
    # Functions _apply_autosave calls
    app.unit_var = types.SimpleNamespace(set=lambda v: None)
    app._calc_wire_length = lambda pts: 0.0
    app._mark_dirty = lambda: None
    app._refresh_symbol_list = lambda: None
    app._refresh_device_list = lambda: None
    app._refresh_wire_list = lambda: None
    app._refresh_bom_tab = lambda: None
    app._redraw = lambda: None
    app.status_label = types.SimpleNamespace(config=lambda **k: None)
    return app


def test_apply_autosave_rejects_non_dict_symbol_types():
    """Corrupted autosave with symbol_types = list shouldn't replace
    the live dict catalog."""
    app = _stub_app()
    original = app.symbol_types
    bad = {"symbol_types": ["this", "should", "have", "been", "a", "dict"]}
    cwt.CameraWireTool._apply_autosave(app, bad)
    assert app.symbol_types is original   # untouched


def test_apply_autosave_rejects_non_dict_cable_types():
    app = _stub_app()
    original = app.cable_types
    bad = {"cable_types": "garbage"}
    cwt.CameraWireTool._apply_autosave(app, bad)
    assert app.cable_types is original


def test_apply_autosave_filters_malformed_device_entries():
    """Device list with non-dict entries or missing id/type should be
    filtered, not crash the load."""
    app = _stub_app()
    cwt.CameraWireTool._apply_autosave(app, {"devices": [
        {"id": "s10", "type": "MD"},           # good
        {"id": "s11"},                         # missing type
        {"type": "CR"},                        # missing id
        "garbage",                             # not a dict
        {"id": "s12", "type": "MD"},           # good
    ]})
    assert {d["id"] for d in app.devices} == {"s10", "s12"}


def test_apply_autosave_tolerates_bad_numeric_fields():
    app = _stub_app()
    cwt.CameraWireTool._apply_autosave(app, {
        "scale_factor": "garbage",
        "next_device_id": "abc",
    })
    # scale_factor stays at original None; next_device_id stays at 100
    assert app.scale_factor is None
    assert app._next_device_id == 100
