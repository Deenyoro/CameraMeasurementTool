"""RotateFovCmd round-trips per-camera azimuth and coalesces consecutive
rotations on the same camera within the time window."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def test_rotate_existing_fov_changes_az():
    dev = {"id": "s10", "type": "MD",
           "fov": {"lens_mm": 4.0, "azimuth_deg": 0.0}}
    cmd = cwt.RotateFovCmd(types.SimpleNamespace(), dev,
                            old_az=0.0, new_az=45.0, timestamp=100.0)
    cmd.do()
    assert dev["fov"]["azimuth_deg"] == 45.0


def test_rotate_undo_restores_az():
    dev = {"id": "s10", "type": "MD",
           "fov": {"lens_mm": 4.0, "azimuth_deg": 90.0}}
    cmd = cwt.RotateFovCmd(types.SimpleNamespace(), dev,
                            old_az=90.0, new_az=180.0, timestamp=100.0)
    cmd.do()
    cmd.undo()
    assert dev["fov"]["azimuth_deg"] == 90.0


def test_rotate_creates_default_fov_when_missing_and_undo_pops_it():
    dev = {"id": "s10", "type": "MD"}   # no fov yet
    cmd = cwt.RotateFovCmd(types.SimpleNamespace(), dev,
                            old_az=0.0, new_az=15.0, timestamp=100.0)
    cmd.do()
    assert "fov" in dev
    assert dev["fov"]["azimuth_deg"] == 15.0
    cmd.undo()
    assert "fov" not in dev


def test_coalesce_within_window_same_camera():
    dev = {"id": "s10", "type": "MD",
           "fov": {"lens_mm": 4.0, "azimuth_deg": 0.0}}
    a = cwt.RotateFovCmd(types.SimpleNamespace(), dev,
                          old_az=0.0, new_az=15.0, timestamp=100.0)
    b = cwt.RotateFovCmd(types.SimpleNamespace(), dev,
                          old_az=15.0, new_az=30.0, timestamp=100.5)
    assert a.can_coalesce_with(b)
    a.absorb(b)
    assert a.new_az == 30.0
    # Old preserved so undo still goes back to ground state
    assert a.old_az == 0.0


def test_no_coalesce_across_cameras():
    dev1 = {"id": "s10", "type": "MD", "fov": {"azimuth_deg": 0.0}}
    dev2 = {"id": "s11", "type": "MD", "fov": {"azimuth_deg": 0.0}}
    a = cwt.RotateFovCmd(types.SimpleNamespace(), dev1,
                          old_az=0.0, new_az=15.0, timestamp=100.0)
    b = cwt.RotateFovCmd(types.SimpleNamespace(), dev2,
                          old_az=0.0, new_az=15.0, timestamp=100.5)
    assert not a.can_coalesce_with(b)


def test_no_coalesce_after_window():
    dev = {"id": "s10", "type": "MD", "fov": {"azimuth_deg": 0.0}}
    a = cwt.RotateFovCmd(types.SimpleNamespace(), dev,
                          old_az=0.0, new_az=15.0, timestamp=100.0)
    b = cwt.RotateFovCmd(types.SimpleNamespace(), dev,
                          old_az=15.0, new_az=30.0, timestamp=105.0)
    assert not a.can_coalesce_with(b)


def test_rotate_replaces_non_dict_fov():
    """A hand-edited device with 'fov': 'corrupted' used to TypeError
    inside .do() (string item-assignment). Should be treated like a
    missing fov: replace with a fresh default + undo pops it."""
    dev = {"id": "s10", "type": "MD", "fov": "this should be a dict"}
    cmd = cwt.RotateFovCmd(types.SimpleNamespace(), dev,
                            old_az=0.0, new_az=15.0, timestamp=100.0)
    cmd.do()
    assert isinstance(dev["fov"], dict)
    assert dev["fov"]["azimuth_deg"] == 15.0
    # Undo treats it as if fov was missing — the corrupted original is
    # gone, but that's better than reinstating bad data.
    cmd.undo()
    assert "fov" not in dev
