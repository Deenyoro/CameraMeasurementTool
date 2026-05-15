"""Tests for CameraWireTool._effective_scale_factor —
project scale × active floor's alignment.scale. The plumbing that
makes 'aligned floors share the calibration scale' actually work."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


def _app(scale_factor=None, active_alignment=None):
    """Minimal stub for _effective_scale_factor + _current_alignment.
    Binds the real _current_alignment method to the stub so the
    plumbing under test actually walks the project's alignment dict
    (without this, _effective_scale_factor's AttributeError fallback
    would mask any bug)."""
    proj = None
    if active_alignment is not None:
        floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                               drawio_file="F1.drawio",
                               alignment=active_alignment)
        proj = mf.ProjectState(active_floor="F1", floors=[floor])
    app = types.SimpleNamespace(scale_factor=scale_factor, _project=proj)
    app._current_alignment = lambda: cwt.CameraWireTool._current_alignment(app)
    return app


def test_returns_none_when_scale_uncalibrated():
    app = _app(scale_factor=None)
    assert cwt.CameraWireTool._effective_scale_factor(app) is None


def test_returns_project_scale_on_identity_alignment():
    app = _app(scale_factor=0.5,
                active_alignment=dict(mf.IDENTITY_ALIGNMENT))
    eff = cwt.CameraWireTool._effective_scale_factor(app)
    assert eff is not None
    assert abs(eff - 0.5) < 1e-9


def test_multiplies_by_alignment_scale():
    """Floor 2 with alignment.scale=1.5 means each raw F2 pixel is
    1.5x the size of a reference pixel, so inches per F2 pixel is
    1.5x the project scale."""
    app = _app(scale_factor=0.5,
                active_alignment={"tx": 0, "ty": 0,
                                    "scale": 1.5, "rotation_deg": 0})
    eff = cwt.CameraWireTool._effective_scale_factor(app)
    assert abs(eff - 0.75) < 1e-9


def test_handles_non_dict_alignment():
    """A corrupted alignment (non-dict) shouldn't crash —
    fall back to identity."""
    floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                          drawio_file="F1.drawio")
    floor.alignment = "garbage"   # corrupted
    proj = mf.ProjectState(active_floor="F1", floors=[floor])
    app = types.SimpleNamespace(scale_factor=0.5, _project=proj)
    app._current_alignment = lambda: cwt.CameraWireTool._current_alignment(app)
    eff = cwt.CameraWireTool._effective_scale_factor(app)
    assert abs(eff - 0.5) < 1e-9


def test_handles_missing_scale_key():
    """Alignment dict missing 'scale' key falls back to 1.0."""
    app = _app(scale_factor=0.5,
                active_alignment={"tx": 100, "ty": 50})
    eff = cwt.CameraWireTool._effective_scale_factor(app)
    assert abs(eff - 0.5) < 1e-9


def test_handles_non_numeric_scale():
    app = _app(scale_factor=0.5,
                active_alignment={"scale": "junk"})
    eff = cwt.CameraWireTool._effective_scale_factor(app)
    assert abs(eff - 0.5) < 1e-9


def test_no_project_returns_project_scale():
    """No multi-floor project at all — _current_alignment returns
    identity, so effective scale = project scale."""
    app = _app(scale_factor=0.5, active_alignment=None)
    # _current_alignment needs _project; with None it returns identity.
    eff = cwt.CameraWireTool._effective_scale_factor(app)
    assert abs(eff - 0.5) < 1e-9
