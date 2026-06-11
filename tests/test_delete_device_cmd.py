"""DeleteDeviceCmd must take its associated wires with it,
and undo must restore both."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _make_app_stub():
    """Lightweight stand-in for CameraWireTool — DeleteDeviceCmd only
    touches `.devices` and `.wire_routes`, so a SimpleNamespace is enough.
    Keeps this test from depending on a Tk root or full app construction."""
    return types.SimpleNamespace(
        devices=[
            {"id": "s10", "type": "MD"},
            {"id": "s11", "type": "MD"},
            {"id": "s12", "type": "CR"},
        ],
        wire_routes=[
            {"device_id": "s10", "points": [(0, 0), (10, 10)]},
            {"device_id": "s11", "points": [(20, 20), (30, 30)]},
            {"device_id": "s10", "points": [(40, 40), (50, 50)]},
            {"device_id": None,  "points": [(60, 60), (70, 70)]},
        ],
    )


def test_delete_device_drops_associated_wires():
    app = _make_app_stub()
    target = app.devices[0]   # s10 has 2 wires
    cmd = cwt.DeleteDeviceCmd(app, target, index=0)
    cmd.do()

    assert target not in app.devices
    # Two wires for s10 are gone; s11's wire and the orphan stay
    assert len(app.wire_routes) == 2
    assert all(w.get("device_id") != "s10" for w in app.wire_routes)


def test_undo_delete_restores_device_and_wires():
    app = _make_app_stub()
    target = app.devices[0]
    original_wires = list(app.wire_routes)
    cmd = cwt.DeleteDeviceCmd(app, target, index=0)
    cmd.do()
    cmd.undo()

    # Device back in original slot
    assert app.devices[0] is target
    # All wires restored
    assert len(app.wire_routes) == len(original_wires)
    # Original wire identities preserved (same dicts, same order)
    for restored, original in zip(app.wire_routes, original_wires, strict=False):
        assert restored is original


def test_delete_device_with_no_wires_is_noop_for_routes():
    app = _make_app_stub()
    target = app.devices[2]   # s12 — no wires
    cmd = cwt.DeleteDeviceCmd(app, target, index=2)
    before = list(app.wire_routes)
    cmd.do()

    assert target not in app.devices
    assert app.wire_routes == before
