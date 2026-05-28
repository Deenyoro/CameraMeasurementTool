"""Tests for the floor-switch viewport-preservation decision.

When floors are aligned onto a shared frame, switching between them must
KEEP the canvas (zoom, offset) so the same physical location stays under
the same pixel. Only un-aligned (all-identity) projects re-fit on switch.
Covers mf.is_identity_alignment + CameraWireTool._floors_share_frame.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


ALIGNED = {"tx": -627.13, "ty": -856.19, "scale": 1.8856, "rotation_deg": 0.0}


def _floor(fid, alignment=None):
    f = mf.FloorState(id=fid, name=fid, elevation_ft=0.0,
                      drawio_file=f"{fid}.drawio")
    if alignment is not None:
        f.alignment = alignment
    return f


def _app(project):
    return types.SimpleNamespace(_project=project)


# ---------------- is_identity_alignment

def test_identity_dict_is_identity():
    assert mf.is_identity_alignment(dict(mf.IDENTITY_ALIGNMENT))


def test_aligned_dict_is_not_identity():
    assert not mf.is_identity_alignment(ALIGNED)


def test_tiny_offset_within_tolerance_is_identity():
    assert mf.is_identity_alignment(
        {"tx": 0.2, "ty": -0.3, "scale": 1.0, "rotation_deg": 0.0})


def test_scale_only_change_is_not_identity():
    assert not mf.is_identity_alignment(
        {"tx": 0.0, "ty": 0.0, "scale": 1.5, "rotation_deg": 0.0})


def test_rotation_only_change_is_not_identity():
    assert not mf.is_identity_alignment(
        {"tx": 0.0, "ty": 0.0, "scale": 1.0, "rotation_deg": 5.0})


def test_corrupt_alignment_counts_as_identity():
    # Nothing meaningful to apply → treat as identity (don't crash).
    for junk in (None, "nope", 42, [], {"scale": "x"}):
        assert mf.is_identity_alignment(junk)


# ---------------- _floors_share_frame

def test_no_project_no_shared_frame():
    app = _app(None)
    assert cwt.CameraWireTool._floors_share_frame(app) is False


def test_all_identity_floors_no_shared_frame():
    proj = mf.ProjectState(active_floor="F1",
                           floors=[_floor("F1"), _floor("F2")])
    app = _app(proj)
    assert cwt.CameraWireTool._floors_share_frame(app) is False


def test_one_aligned_floor_means_shared_frame():
    # F1 is the identity reference; F2 carries the alignment. The project
    # shares a frame even though F1 itself is identity — this is the case
    # that the old per-target check got wrong (it re-fit on return to F1).
    proj = mf.ProjectState(
        active_floor="F1",
        floors=[_floor("F1"), _floor("F2", alignment=dict(ALIGNED))])
    app = _app(proj)
    assert cwt.CameraWireTool._floors_share_frame(app) is True


def test_shared_frame_holds_regardless_of_active_floor():
    # Returning to the identity reference floor must still report a shared
    # frame so the viewport is preserved rather than re-fit.
    proj = mf.ProjectState(
        active_floor="F2",
        floors=[_floor("F1"), _floor("F2", alignment=dict(ALIGNED))])
    app = _app(proj)
    assert cwt.CameraWireTool._floors_share_frame(app) is True
