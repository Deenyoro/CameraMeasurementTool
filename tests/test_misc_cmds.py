"""Round-trip tests for the remaining untested commands:
AddDeviceCmd, ClearDeviceWiresCmd, EditSymbolCmd, CalibrateCmd."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


# ---------------- AddDeviceCmd
def test_add_device_appends_and_undo_removes():
    app = types.SimpleNamespace(devices=[])
    dev = {"id": "s10", "type": "MD"}
    cmd = cwt.AddDeviceCmd(app, dev)
    cmd.do()
    assert app.devices == [dev]
    cmd.undo()
    assert app.devices == []


def test_add_device_redo_idempotent():
    app = types.SimpleNamespace(devices=[])
    dev = {"id": "s10", "type": "MD"}
    cmd = cwt.AddDeviceCmd(app, dev)
    cmd.do(); cmd.do()
    assert sum(1 for d in app.devices if d["id"] == "s10") == 1


# ---------------- ClearDeviceWiresCmd
def test_clear_device_wires_removes_only_target():
    app = types.SimpleNamespace(wire_routes=[
        {"device_id": "s10", "points": [(0, 0), (1, 1)]},
        {"device_id": "s11", "points": [(2, 2), (3, 3)]},
        {"device_id": "s10", "points": [(4, 4), (5, 5)]},
    ])
    cmd = cwt.ClearDeviceWiresCmd(app, "s10")
    cmd.do()
    assert all(w["device_id"] != "s10" for w in app.wire_routes)
    assert any(w["device_id"] == "s11" for w in app.wire_routes)


def test_clear_device_wires_undo_restores():
    original_a = {"device_id": "s10", "points": [(0, 0), (1, 1)]}
    original_b = {"device_id": "s11", "points": [(2, 2), (3, 3)]}
    original_c = {"device_id": "s10", "points": [(4, 4), (5, 5)]}
    app = types.SimpleNamespace(wire_routes=[original_a, original_b, original_c])
    cmd = cwt.ClearDeviceWiresCmd(app, "s10")
    cmd.do()
    cmd.undo()
    # Order may not be preserved (extend appends), but identity is
    ids = {id(w) for w in app.wire_routes}
    assert id(original_a) in ids
    assert id(original_b) in ids
    assert id(original_c) in ids


# ---------------- EditSymbolCmd
def test_edit_symbol_in_place_change():
    app = types.SimpleNamespace(
        symbol_types={"MD": {"label": "Mini", "color": "#FFFF00",
                              "shape": "ellipse", "is_camera": True}},
        devices=[{"id": "s1", "type": "MD"}],
    )
    old_info = dict(app.symbol_types["MD"])
    new_info = {"label": "Minidome", "color": "#FFA500",
                "shape": "ellipse", "is_camera": True}
    cmd = cwt.EditSymbolCmd(app, "MD", old_info, "MD", new_info, is_new=False)
    cmd.do()
    assert app.symbol_types["MD"] == new_info
    cmd.undo()
    assert app.symbol_types["MD"] == old_info


def test_edit_symbol_rename_propagates_to_devices():
    """Renaming MD → MDX should also retag any devices using the old code."""
    app = types.SimpleNamespace(
        symbol_types={"MD": {"label": "Mini", "color": "#FFFF00",
                              "shape": "ellipse", "is_camera": True}},
        devices=[{"id": "s1", "type": "MD"}, {"id": "s2", "type": "CR"}],
    )
    old_info = dict(app.symbol_types["MD"])
    new_info = dict(old_info); new_info["label"] = "New label"
    cmd = cwt.EditSymbolCmd(app, "MD", old_info, "MDX", new_info, is_new=False)
    cmd.do()
    assert "MDX" in app.symbol_types
    assert "MD" not in app.symbol_types
    assert app.devices[0]["type"] == "MDX"
    assert app.devices[1]["type"] == "CR"   # untouched
    cmd.undo()
    assert "MD" in app.symbol_types
    assert app.devices[0]["type"] == "MD"


def test_edit_symbol_is_new_only_runs_do_path():
    """is_new=True means undo must remove the freshly-added type, not
    restore an old value."""
    app = types.SimpleNamespace(symbol_types={}, devices=[])
    info = {"label": "New", "color": "#FFFF00", "shape": "ellipse",
            "is_camera": False}
    cmd = cwt.EditSymbolCmd(app, "NEW", info, "NEW", info, is_new=True)
    cmd.do()
    assert "NEW" in app.symbol_types
    cmd.undo()
    assert "NEW" not in app.symbol_types


def test_edit_symbol_tolerates_non_dict_old_info():
    """A corrupted catalog entry (non-dict) passed as old_info used to
    crash __init__ on dict() iteration. Now snapshots an empty dict so
    undo can still re-establish the slot without reinstating garbage."""
    app = types.SimpleNamespace(
        symbol_types={"MD": "should have been a dict"}, devices=[])
    new_info = {"label": "Minidome", "shape": "ellipse",
                "color": "#FFFF00", "is_camera": True}
    cmd = cwt.EditSymbolCmd(app, "MD", "should have been a dict",
                              "MD", new_info, is_new=False)
    cmd.do()
    assert app.symbol_types["MD"] == new_info
    cmd.undo()
    # Undo restores the snapshot — but since old was corrupted, snapshot
    # is {} not the garbage string. Better than crashing on dict('...').
    assert app.symbol_types["MD"] == {}


def test_delete_symbol_tolerates_non_dict_info():
    """Mirror — DeleteSymbolCmd's snapshot was 'dict(info)' which would
    crash on a corrupted entry. Now substitutes empty dict so undo
    re-establishes the slot for the user to fix."""
    app = types.SimpleNamespace(
        symbol_types={"BAD": "corrupted"}, devices=[], wire_routes=[])
    cmd = cwt.DeleteSymbolCmd(app, "BAD", "corrupted", [])
    cmd.do()
    assert "BAD" not in app.symbol_types
    cmd.undo()
    # Re-established with empty placeholder; user can edit the row.
    assert app.symbol_types["BAD"] == {}


# ---------------- CalibrateCmd
def test_calibrate_round_trip():
    """CalibrateCmd swaps scale_factor and triggers _recalc_all_wires."""
    recalcs = []
    app = types.SimpleNamespace(scale_factor=None)
    app._recalc_all_wires = lambda: recalcs.append(app.scale_factor)
    cmd = cwt.CalibrateCmd(app, old_scale=None, new_scale=0.6)
    cmd.do()
    assert app.scale_factor == 0.6
    assert recalcs == [0.6]
    cmd.undo()
    assert app.scale_factor is None
    assert recalcs == [0.6, None]
