"""PasteDevicesCmd round-trips a batch of new devices through undo,
preserves them as the same object identities, and is idempotent on
redo (don't double-append)."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub():
    return types.SimpleNamespace(devices=[
        {"id": "s10", "type": "MD"},
    ])


def test_paste_appends_each_new_device():
    app = _stub()
    new = [{"id": "s20", "type": "MD"}, {"id": "s21", "type": "CR"}]
    cmd = cwt.PasteDevicesCmd(app, new)
    cmd.do()
    assert {d["id"] for d in app.devices} == {"s10", "s20", "s21"}


def test_undo_removes_pasted_devices_only():
    app = _stub()
    new = [{"id": "s20", "type": "MD"}, {"id": "s21", "type": "CR"}]
    cmd = cwt.PasteDevicesCmd(app, new)
    cmd.do()
    cmd.undo()
    # Original device kept, pasted ones gone
    assert {d["id"] for d in app.devices} == {"s10"}


def test_redo_does_not_duplicate_pasted_devices():
    """do() guards against double-append by checking 'd not in devices'.
    Important because a redo call shouldn't add the same dict twice."""
    app = _stub()
    new = [{"id": "s20", "type": "MD"}]
    cmd = cwt.PasteDevicesCmd(app, new)
    cmd.do()
    cmd.do()   # idempotent
    assert sum(1 for d in app.devices if d["id"] == "s20") == 1


def test_undo_then_redo_round_trip():
    app = _stub()
    new = [{"id": "s20", "type": "MD"}]
    cmd = cwt.PasteDevicesCmd(app, new)
    cmd.do()
    cmd.undo()
    cmd.do()
    assert any(d["id"] == "s20" for d in app.devices)
