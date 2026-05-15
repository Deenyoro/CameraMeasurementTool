"""Tests for CameraWireTool._maybe_warn_drastic_alignment — the
startup safety net that catches a 'wrong direction apply' from a
previous session and offers a one-click reset."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


class _StubScaleLabel:
    def __init__(self):
        self.text = ""

    def config(self, **kw):
        if "text" in kw:
            self.text = kw["text"]


def _app(alignment, mock_answer):
    """Build a stub for the warning method. mock_answer drives the
    yesnocancel popup: True/False/None for yes/no/cancel."""
    floor = mf.FloorState(id="F1", name="First", elevation_ft=0.0,
                           drawio_file="F1.drawio",
                           alignment=dict(alignment))
    proj = mf.ProjectState(active_floor="F1", floors=[floor])
    app = types.SimpleNamespace(_project=proj,
                                  project_path="/tmp/nope.json",
                                  scale_factor=0.5,
                                  scale_label=_StubScaleLabel())
    app._dirty_marked = 0
    app._mark_dirty = lambda: setattr(app, "_dirty_marked",
                                       app._dirty_marked + 1)
    app._fit_image = lambda: None
    app._recalc_all_wires = lambda: None
    app._refresh_wire_list = lambda: None
    app._refresh_bom_tab = lambda: None
    app._redraw = lambda: None
    app._flash_status = lambda msg, ttl_ms=None: None
    app._scale_label_text = lambda: "Scale: 1 px = 0.5\""
    return app, floor


def _patch_save(monkeypatch):
    """Stub mf_mod.save_project so the test doesn't write to disk."""
    monkeypatch.setattr(cwt.mf_mod, "save_project",
                         lambda *_a, **_k: None)


def test_identity_alignment_no_warning(monkeypatch):
    asked = []
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel",
                         lambda *_a, **_k: asked.append("called") or True)
    _patch_save(monkeypatch)
    app, _ = _app(mf.IDENTITY_ALIGNMENT, mock_answer=True)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert asked == []   # no warning popped


def test_minor_alignment_no_warning(monkeypatch):
    """1.2x scale is within [0.6, 1.7] — shouldn't trigger the warn."""
    asked = []
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel",
                         lambda *_a, **_k: asked.append("called") or True)
    _patch_save(monkeypatch)
    app, _ = _app({"tx": 0, "ty": 0, "scale": 1.2, "rotation_deg": 5},
                   mock_answer=True)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert asked == []


def test_drastic_scale_warns(monkeypatch):
    """0.5335 scale (the user's actual bad state) must trigger."""
    asked = []
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel",
                         lambda *_a, **_k: asked.append("called") or False)
    _patch_save(monkeypatch)
    app, _ = _app({"tx": 324, "ty": 450, "scale": 0.5335,
                    "rotation_deg": 0}, mock_answer=False)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert asked == ["called"]


def test_drastic_rotation_warns(monkeypatch):
    """Rotation > 20° triggers, even with a normal scale."""
    asked = []
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel",
                         lambda *_a, **_k: asked.append("called") or False)
    _patch_save(monkeypatch)
    app, _ = _app({"tx": 0, "ty": 0, "scale": 1.0,
                    "rotation_deg": 30}, mock_answer=False)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert asked == ["called"]


def test_yes_resets_alignment_and_marks_dirty(monkeypatch):
    """User answered YES — alignment should be reset to identity AND
    the project should be marked dirty + saved."""
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel",
                         lambda *_a, **_k: True)
    saved = []
    monkeypatch.setattr(cwt.mf_mod, "save_project",
                         lambda proj, path: saved.append((proj, path)))
    app, floor = _app({"tx": 100, "ty": 200, "scale": 0.5,
                        "rotation_deg": 0}, mock_answer=True)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert floor.alignment == mf.IDENTITY_ALIGNMENT
    assert app._dirty_marked == 1
    assert len(saved) == 1


def test_no_keeps_alignment(monkeypatch):
    """User answered NO — alignment must stay as is."""
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel",
                         lambda *_a, **_k: False)
    _patch_save(monkeypatch)
    bad = {"tx": 100, "ty": 200, "scale": 0.5, "rotation_deg": 0}
    app, floor = _app(bad, mock_answer=False)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert floor.alignment == bad
    assert app._dirty_marked == 0


def test_cancel_keeps_alignment(monkeypatch):
    """User hit Cancel — same as NO for this session."""
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel",
                         lambda *_a, **_k: None)
    _patch_save(monkeypatch)
    bad = {"tx": 100, "ty": 200, "scale": 0.5, "rotation_deg": 0}
    app, floor = _app(bad, mock_answer=None)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert floor.alignment == bad
    assert app._dirty_marked == 0


def test_no_project_no_warning(monkeypatch):
    asked = []
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel",
                         lambda *_a, **_k: asked.append("called") or True)
    _patch_save(monkeypatch)
    app = types.SimpleNamespace(_project=None)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert asked == []
