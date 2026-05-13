"""EditCableTypeCmd round-trips cable-catalog changes (add / edit /
delete) through the undo stack."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub():
    return types.SimpleNamespace(cable_types={
        "Cat6": {"label": "Cat6 PoE", "color": "#0080FF",
                  "cost_per_ft": 0.18},
    })


def test_add_new_cable_type():
    app = _stub()
    new = {"label": "Fiber OM4", "color": "#FF8000", "cost_per_ft": 1.20}
    cmd = cwt.EditCableTypeCmd(app, "OM4", old_info=None, new_info=new)
    cmd.do()
    assert app.cable_types["OM4"] == new


def test_undo_add_removes_key():
    app = _stub()
    new = {"label": "Fiber OM4", "color": "#FF8000", "cost_per_ft": 1.20}
    cmd = cwt.EditCableTypeCmd(app, "OM4", old_info=None, new_info=new)
    cmd.do()
    cmd.undo()
    assert "OM4" not in app.cable_types


def test_edit_existing_cable_type():
    app = _stub()
    old = dict(app.cable_types["Cat6"])
    new = {"label": "Cat6 23AWG", "color": "#0066CC", "cost_per_ft": 0.22}
    cmd = cwt.EditCableTypeCmd(app, "Cat6", old_info=old, new_info=new)
    cmd.do()
    assert app.cable_types["Cat6"] == new
    cmd.undo()
    assert app.cable_types["Cat6"] == old


def test_delete_cable_type():
    app = _stub()
    old = dict(app.cable_types["Cat6"])
    cmd = cwt.EditCableTypeCmd(app, "Cat6", old_info=old, new_info=None)
    cmd.do()
    assert "Cat6" not in app.cable_types
    cmd.undo()
    assert app.cable_types["Cat6"] == old


def test_redo_after_undo():
    app = _stub()
    new = {"label": "RG6", "color": "#101010", "cost_per_ft": 0.55}
    cmd = cwt.EditCableTypeCmd(app, "RG6", old_info=None, new_info=new)
    cmd.do()
    cmd.undo()
    cmd.do()
    assert app.cable_types["RG6"] == new


def test_non_dict_old_info_treated_as_no_prior_value():
    """A corrupted (non-dict) catalog entry passed as old_info should
    NOT be snapshot for undo — dict(non_dict) would crash. Treat the
    edit as if there was no prior value, so undo cleans up the bad
    state by popping the key rather than reinstating the corruption."""
    app = _stub()
    new = {"label": "Fixed", "color": "#0080FF", "cost_per_ft": 0.20}
    cmd = cwt.EditCableTypeCmd(app, "BadKey",
                                 old_info="this should have been a dict",
                                 new_info=new)
    cmd.do()
    assert app.cable_types["BadKey"] == new
    cmd.undo()
    # Corrupted 'old' is gone; undo popped the key rather than crashing
    # on dict('this should have been a dict').
    assert "BadKey" not in app.cable_types


def test_non_dict_new_info_treated_as_delete():
    """Symmetric guard — a non-dict new_info shouldn't write garbage
    into the catalog. Treat it as a delete."""
    app = _stub()
    old = dict(app.cable_types["Cat6"])
    cmd = cwt.EditCableTypeCmd(app, "Cat6",
                                 old_info=old,
                                 new_info=12345)  # not a dict
    cmd.do()
    # Treated as a delete since new_info wasn't dict-shaped
    assert "Cat6" not in app.cable_types
    cmd.undo()
    assert app.cable_types["Cat6"] == old
