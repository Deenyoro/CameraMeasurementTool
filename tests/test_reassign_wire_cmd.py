"""ReassignWireCmd round-trips device_id and is undoable."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def test_reassign_changes_device_id():
    route = {"device_id": "s10", "points": [(0, 0), (1, 1)]}
    app = types.SimpleNamespace()
    cmd = cwt.ReassignWireCmd(app, route, old_did="s10", new_did="s11")
    cmd.do()
    assert route["device_id"] == "s11"


def test_reassign_undo_restores_device_id():
    route = {"device_id": "s10", "points": [(0, 0), (1, 1)]}
    app = types.SimpleNamespace()
    cmd = cwt.ReassignWireCmd(app, route, old_did="s10", new_did="s11")
    cmd.do()
    cmd.undo()
    assert route["device_id"] == "s10"


def test_reassign_can_target_unassigned():
    route = {"device_id": "s10", "points": [(0, 0), (1, 1)]}
    app = types.SimpleNamespace()
    cmd = cwt.ReassignWireCmd(app, route, old_did="s10", new_did=None)
    cmd.do()
    assert route["device_id"] is None
    cmd.undo()
    assert route["device_id"] == "s10"
