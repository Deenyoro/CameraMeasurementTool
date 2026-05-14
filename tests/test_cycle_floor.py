"""Tests for CameraWireTool._cycle_floor — the PgUp/PgDn floor switcher.
Wraps both ends, no-ops without a project or with <2 floors."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


def _floor(fid):
    return mf.FloorState(id=fid, name=fid, elevation_ft=0.0,
                          drawio_file=f"{fid}.drawio")


def _app(project):
    app = types.SimpleNamespace(_project=project)
    app._switched = []
    app._switch_floor = lambda fid: app._switched.append(fid)
    return app


def test_no_project_no_op():
    app = _app(None)
    cwt.CameraWireTool._cycle_floor(app, 1)
    assert app._switched == []


def test_one_floor_no_op():
    proj = mf.ProjectState(active_floor="F1", floors=[_floor("F1")])
    app = _app(proj)
    cwt.CameraWireTool._cycle_floor(app, 1)
    assert app._switched == []


def test_next_advances():
    proj = mf.ProjectState(
        active_floor="F1",
        floors=[_floor("F1"), _floor("F2"), _floor("F3")])
    app = _app(proj)
    cwt.CameraWireTool._cycle_floor(app, 1)
    assert app._switched == ["F2"]


def test_prev_goes_back():
    proj = mf.ProjectState(
        active_floor="F2",
        floors=[_floor("F1"), _floor("F2"), _floor("F3")])
    app = _app(proj)
    cwt.CameraWireTool._cycle_floor(app, -1)
    assert app._switched == ["F1"]


def test_wraps_off_end():
    proj = mf.ProjectState(
        active_floor="F3",
        floors=[_floor("F1"), _floor("F2"), _floor("F3")])
    app = _app(proj)
    cwt.CameraWireTool._cycle_floor(app, 1)
    assert app._switched == ["F1"]


def test_wraps_off_start():
    proj = mf.ProjectState(
        active_floor="F1",
        floors=[_floor("F1"), _floor("F2"), _floor("F3")])
    app = _app(proj)
    cwt.CameraWireTool._cycle_floor(app, -1)
    assert app._switched == ["F3"]


def test_active_floor_not_in_list_no_op():
    """Stale active_floor pointing at a deleted floor must not crash."""
    proj = mf.ProjectState(
        active_floor="F_GONE",
        floors=[_floor("F1"), _floor("F2")])
    app = _app(proj)
    cwt.CameraWireTool._cycle_floor(app, 1)
    assert app._switched == []
