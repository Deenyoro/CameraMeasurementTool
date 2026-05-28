"""Tests for CameraWireTool._maybe_warn_drastic_alignment.

This used to offer a one-click reset that SAVED over the project — which
destroyed a valid 1.886x cross-floor alignment. It is now a purely
informational nudge: it NEVER mutates the alignment, marks dirty, or
saves, and only fires for genuinely extreme (off-page) transforms.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


def _app(alignment):
    floor = mf.FloorState(id="F1", name="First", elevation_ft=0.0,
                          drawio_file="F1.drawio", alignment=dict(alignment))
    proj = mf.ProjectState(active_floor="F1", floors=[floor])
    app = types.SimpleNamespace(_project=proj, project_path="/tmp/nope.json")
    app._dirty_marked = 0
    app._mark_dirty = lambda: setattr(app, "_dirty_marked",
                                      app._dirty_marked + 1)
    app._flashes = []
    app._flash_status = lambda msg, ttl_ms=None: app._flashes.append(msg)
    return app, floor


def _guard_destructive(monkeypatch):
    """Fail loudly if the method ever pops a dialog or writes to disk."""
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            AssertionError("must not prompt")))
    monkeypatch.setattr(cwt.mf_mod, "save_project",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            AssertionError("must not save")))


def test_identity_no_warning(monkeypatch):
    _guard_destructive(monkeypatch)
    app, _ = _app(mf.IDENTITY_ALIGNMENT)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert app._flashes == []


def test_minor_alignment_no_warning(monkeypatch):
    _guard_destructive(monkeypatch)
    app, _ = _app({"tx": 0, "ty": 0, "scale": 1.2, "rotation_deg": 5})
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert app._flashes == []


def test_legit_large_cross_floor_scale_no_warning(monkeypatch):
    """The regression guard: a 1.886x alignment is a valid cross-floor
    map and must NOT warn or be touched."""
    _guard_destructive(monkeypatch)
    al = {"tx": -627.1, "ty": -856.2, "scale": 1.8856, "rotation_deg": 0}
    app, floor = _app(al)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert app._flashes == []
    assert floor.alignment == al          # untouched
    assert app._dirty_marked == 0


def test_moderate_shrink_no_warning(monkeypatch):
    """0.5335x (which the OLD code flagged) is within the new normal range."""
    _guard_destructive(monkeypatch)
    app, _ = _app({"tx": 324, "ty": 450, "scale": 0.5335, "rotation_deg": 0})
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert app._flashes == []


def test_extreme_scale_warns_but_does_not_mutate(monkeypatch):
    _guard_destructive(monkeypatch)
    al = {"tx": 0, "ty": 0, "scale": 0.02, "rotation_deg": 0}  # 50x shrink
    app, floor = _app(al)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert len(app._flashes) == 1          # informed
    assert floor.alignment == al           # but NOT reset
    assert app._dirty_marked == 0          # and NOT marked dirty


def test_extreme_rotation_warns_but_does_not_mutate(monkeypatch):
    _guard_destructive(monkeypatch)
    al = {"tx": 0, "ty": 0, "scale": 1.0, "rotation_deg": 120}
    app, floor = _app(al)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    assert len(app._flashes) == 1
    assert floor.alignment == al
    assert app._dirty_marked == 0


def test_no_project_no_warning(monkeypatch):
    _guard_destructive(monkeypatch)
    app = types.SimpleNamespace(_project=None)
    cwt.CameraWireTool._maybe_warn_drastic_alignment(app)
    # nothing to assert beyond "didn't raise / didn't prompt"
