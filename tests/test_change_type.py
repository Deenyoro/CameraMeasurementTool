"""Tests for ChangeDeviceTypeCmd — bulk/single device retype with undo."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _app(symbol_types):
    app = types.SimpleNamespace(symbol_types=symbol_types)
    app._safe_sym = lambda c: (symbol_types.get(c)
                               if isinstance(symbol_types.get(c), dict) else {})
    return app


def test_bulk_change_sets_type_and_style():
    syms = {"MD": {"shape": "ellipse", "color": "#FFFF00", "is_camera": True},
            "BC": {"shape": "rect", "color": "#00FF00", "is_camera": True}}
    app = _app(syms)
    devs = [{"id": "s1", "type": "MD", "orig_style": "old1"},
            {"id": "s2", "type": "MD", "orig_style": "old2"}]
    cmd = cwt.ChangeDeviceTypeCmd(app, devs, "BC")
    cmd.do()
    assert [d["type"] for d in devs] == ["BC", "BC"]
    # New style regenerated from BC's shape/color.
    assert all("#00FF00" in d["orig_style"] for d in devs)
    assert all("rounded=0" in d["orig_style"] for d in devs)  # rect template


def test_undo_restores_type_and_style():
    syms = {"MD": {"shape": "ellipse", "color": "#FFFF00"},
            "BC": {"shape": "rect", "color": "#00FF00"}}
    app = _app(syms)
    devs = [{"id": "s1", "type": "MD", "orig_style": "ORIGINAL"}]
    cmd = cwt.ChangeDeviceTypeCmd(app, devs, "BC")
    cmd.do()
    assert devs[0]["type"] == "BC"
    cmd.undo()
    assert devs[0]["type"] == "MD"
    assert devs[0]["orig_style"] == "ORIGINAL"


def test_undo_removes_style_when_originally_absent():
    syms = {"MD": {"shape": "ellipse", "color": "#FFFF00"},
            "BC": {"shape": "rect", "color": "#00FF00"}}
    app = _app(syms)
    devs = [{"id": "s1", "type": "MD"}]   # no orig_style
    cmd = cwt.ChangeDeviceTypeCmd(app, devs, "BC")
    cmd.do()
    assert "orig_style" in devs[0]
    cmd.undo()
    assert devs[0]["type"] == "MD"
    assert "orig_style" not in devs[0]


def test_skips_non_dict_entries():
    syms = {"BC": {"shape": "rect", "color": "#00FF00"}}
    app = _app(syms)
    devs = ["junk", {"id": "s1", "type": "MD"}]
    cmd = cwt.ChangeDeviceTypeCmd(app, devs, "BC")
    cmd.do()  # must not raise
    assert devs[1]["type"] == "BC"
