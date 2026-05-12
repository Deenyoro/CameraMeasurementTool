"""ClearFovCmd snapshots and restores per-camera FOV dicts."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def test_clear_pops_fov_from_each_device():
    devs = [
        {"id": "s1", "type": "MD", "fov": {"lens_mm": 4.0, "azimuth_deg": 45}},
        {"id": "s2", "type": "MD", "fov": {"lens_mm": 6.0, "azimuth_deg": 90}},
        {"id": "s3", "type": "CR"},   # no fov, shouldn't be passed in
    ]
    targets = [d for d in devs if d.get("fov")]
    cmd = cwt.ClearFovCmd(types.SimpleNamespace(), targets)
    cmd.do()
    assert "fov" not in devs[0]
    assert "fov" not in devs[1]


def test_undo_restores_each_devices_original_fov():
    fov_a = {"lens_mm": 4.0, "azimuth_deg": 45}
    fov_b = {"lens_mm": 6.0, "azimuth_deg": 90}
    devs = [
        {"id": "s1", "type": "MD", "fov": dict(fov_a)},
        {"id": "s2", "type": "MD", "fov": dict(fov_b)},
    ]
    cmd = cwt.ClearFovCmd(types.SimpleNamespace(), list(devs))
    cmd.do()
    cmd.undo()
    assert devs[0]["fov"] == fov_a
    assert devs[1]["fov"] == fov_b


def test_redo_after_undo_clears_again():
    devs = [{"id": "s1", "type": "MD", "fov": {"lens_mm": 4.0}}]
    cmd = cwt.ClearFovCmd(types.SimpleNamespace(), list(devs))
    cmd.do()
    cmd.undo()
    cmd.do()
    assert "fov" not in devs[0]


def test_clear_drops_non_dict_fov_without_snapshot():
    """If a device's 'fov' is a corrupted non-dict (hand-edited project
    file), ClearFovCmd.do should pop the key without trying to snapshot
    it — dict(str) would crash on iteration. Undo doesn't try to
    restore the corrupt value either."""
    devs = [
        {"id": "s1", "type": "MD", "fov": "corrupted-string"},
        {"id": "s2", "type": "MD", "fov": {"lens_mm": 6.0}},  # well-formed
    ]
    cmd = cwt.ClearFovCmd(types.SimpleNamespace(), list(devs))
    cmd.do()
    assert "fov" not in devs[0]
    assert "fov" not in devs[1]
    cmd.undo()
    # Well-formed entry comes back; corrupted one stays gone.
    assert "fov" not in devs[0]
    assert devs[1]["fov"] == {"lens_mm": 6.0}
