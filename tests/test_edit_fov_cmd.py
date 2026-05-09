"""EditFovCmd round-trips per-camera FOV settings, including the
'apply to all of type' bulk path. Critical because the FOV dialog
is the most-touched UI in the app and was a silent direct mutation."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def test_edit_fov_single_replaces_dict():
    dev = {"id": "s10", "type": "MD",
           "fov": {"lens_mm": 4.0, "azimuth_deg": 0.0}}
    new_fov = {"lens_mm": 6.0, "azimuth_deg": 45.0}
    cmd = cwt.EditFovCmd(types.SimpleNamespace(),
                          [(dev, dict(dev["fov"]), new_fov)])
    cmd.do()
    assert dev["fov"]["lens_mm"] == 6.0
    assert dev["fov"]["azimuth_deg"] == 45.0


def test_edit_fov_undo_restores_old_dict():
    original = {"lens_mm": 4.0, "azimuth_deg": 0.0}
    dev = {"id": "s10", "type": "MD", "fov": dict(original)}
    new_fov = {"lens_mm": 6.0, "azimuth_deg": 45.0}
    cmd = cwt.EditFovCmd(types.SimpleNamespace(),
                          [(dev, dict(original), new_fov)])
    cmd.do()
    cmd.undo()
    assert dev["fov"] == original


def test_edit_fov_undo_removes_key_when_old_was_none():
    dev = {"id": "s10", "type": "MD"}   # no fov yet
    new_fov = {"lens_mm": 4.0, "azimuth_deg": 0.0}
    cmd = cwt.EditFovCmd(types.SimpleNamespace(), [(dev, None, new_fov)])
    cmd.do()
    assert "fov" in dev
    cmd.undo()
    assert "fov" not in dev


def test_edit_fov_bulk_path():
    devs = [{"id": f"s{i}", "type": "MD",
             "fov": {"lens_mm": 4.0, "azimuth_deg": float(i * 10)}}
            for i in range(3)]
    entries = []
    for d in devs:
        old = dict(d["fov"])
        new = dict(old); new["lens_mm"] = 8.0
        entries.append((d, old, new))
    cmd = cwt.EditFovCmd(types.SimpleNamespace(), entries)
    cmd.do()
    assert all(d["fov"]["lens_mm"] == 8.0 for d in devs)
    # Per-camera azimuth preserved
    assert [d["fov"]["azimuth_deg"] for d in devs] == [0.0, 10.0, 20.0]
    cmd.undo()
    assert all(d["fov"]["lens_mm"] == 4.0 for d in devs)
