"""Direct tests for CameraWireTool._alignment_scale — the
single-line accessor that 8 call sites depend on for their
alignment-aware threshold / scaling math."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


def _app(alignment):
    """Build a minimal stub bound to the real _current_alignment."""
    if alignment is None:
        proj = None
    else:
        floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                               drawio_file="F1.drawio")
        floor.alignment = alignment   # let through non-dict for defense tests
        proj = mf.ProjectState(active_floor="F1", floors=[floor])
    app = types.SimpleNamespace(_project=proj)
    app._current_alignment = lambda: (
        cwt.CameraWireTool._current_alignment(app))
    return app


def test_identity_returns_1():
    assert cwt.CameraWireTool._alignment_scale(
        _app(dict(mf.IDENTITY_ALIGNMENT))) == 1.0


def test_no_project_returns_1():
    """Pre-multifloor / standalone mode: no project → identity."""
    assert cwt.CameraWireTool._alignment_scale(_app(None)) == 1.0


def test_returns_alignment_scale_value():
    assert cwt.CameraWireTool._alignment_scale(
        _app({"tx": 0, "ty": 0, "scale": 1.5, "rotation_deg": 0})
    ) == 1.5


def test_missing_scale_key_returns_1():
    """Partial alignment dict (missing 'scale') falls back to 1.0."""
    assert cwt.CameraWireTool._alignment_scale(
        _app({"tx": 100, "ty": 50})   # no scale key
    ) == 1.0


def test_non_numeric_scale_returns_1():
    """A 'scale': 'junk' from hand-edited file shouldn't crash."""
    assert cwt.CameraWireTool._alignment_scale(
        _app({"scale": "junk"})
    ) == 1.0


def test_zero_scale_returns_1():
    """A zero scale would divide-by-zero in most call sites — the helper
    swaps it for 1.0 via the `or 1.0` short-circuit."""
    assert cwt.CameraWireTool._alignment_scale(
        _app({"scale": 0})
    ) == 1.0


def test_negative_scale_passes_through():
    """A negative scale is unusual but mathematically valid (mirrored
    floor). Don't filter it — the user might have done this deliberately."""
    assert cwt.CameraWireTool._alignment_scale(
        _app({"scale": -2.0})
    ) == -2.0


def test_corrupted_alignment_returns_1():
    """Non-dict alignment (None, str, int) falls back to 1.0."""
    for bad in (None, "junk", 42, [1, 2, 3]):
        app = _app(bad)
        assert cwt.CameraWireTool._alignment_scale(app) == 1.0, f"bad={bad!r}"
