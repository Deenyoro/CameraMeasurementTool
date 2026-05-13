"""Tests for CameraWireTool._can_navigate_away — the gate that runs
before window-close / Open / Recent / floor-switch destroys in-memory
state. Verifies both checks (mid-draw + dirty) fire in the right order
and that the user's Cancel halts the navigation."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub(current_wire_points=None, dirty=False):
    return types.SimpleNamespace(
        current_wire_points=list(current_wire_points or []),
        _dirty=dirty,
    )


def test_returns_true_when_nothing_pending():
    """No wire-in-progress, project clean → no prompts, just True."""
    app = _stub()
    assert cwt.CameraWireTool._can_navigate_away(app) is True


def test_warns_about_wire_in_progress_and_clears_on_confirm(monkeypatch):
    """User confirmed discard → method clears current_wire_points and
    returns True so navigation proceeds."""
    app = _stub(current_wire_points=[(0, 0), (10, 10), (20, 5)])
    asked = {"calls": 0, "title": None}
    def fake_askyesno(title, message):
        asked["calls"] += 1
        asked["title"] = title
        return True
    monkeypatch.setattr(cwt.messagebox, "askyesno", fake_askyesno)
    assert cwt.CameraWireTool._can_navigate_away(app) is True
    assert asked["calls"] == 1
    assert "Discard wire in progress" in asked["title"]
    # Points cleared so the wire doesn't haunt the next floor / file.
    assert app.current_wire_points == []


def test_returns_false_when_user_cancels_wire_discard(monkeypatch):
    """User cancelled the mid-draw discard → method returns False AND
    leaves the wire points alone so the user can finish drawing."""
    app = _stub(current_wire_points=[(0, 0), (10, 10)])
    monkeypatch.setattr(cwt.messagebox, "askyesno",
                         lambda *_a, **_k: False)
    assert cwt.CameraWireTool._can_navigate_away(app) is False
    assert app.current_wire_points == [(0, 0), (10, 10)]


def test_dirty_check_only_runs_if_wire_check_passed(monkeypatch):
    """If the wire-discard prompt is cancelled, we should NOT also
    pop the unsaved-changes prompt — return False straight away."""
    app = _stub(current_wire_points=[(0, 0), (1, 1)], dirty=True)
    monkeypatch.setattr(cwt.messagebox, "askyesno",
                         lambda *_a, **_k: False)
    # _confirm_discard would be called if we got that far — replace it
    # with a marker that records being called.
    confirm_calls = {"n": 0}
    app._confirm_discard = lambda: confirm_calls.__setitem__("n",
                                                              confirm_calls["n"] + 1) or True
    assert cwt.CameraWireTool._can_navigate_away(app) is False
    # Wire prompt cancelled → dirty path never reached
    assert confirm_calls["n"] == 0


def test_dirty_only_no_wire(monkeypatch):
    """No wire in progress, project dirty → only the dirty prompt fires."""
    app = _stub(dirty=True)
    called = {"confirm": 0}
    def fake_confirm():
        called["confirm"] += 1
        return True
    app._confirm_discard = fake_confirm
    # askyesno should NOT be called since current_wire_points is empty
    monkeypatch.setattr(cwt.messagebox, "askyesno",
                         lambda *_a, **_k: pytest_fail())
    def pytest_fail():
        raise AssertionError("askyesno called when no wire in progress")
    assert cwt.CameraWireTool._can_navigate_away(app) is True
    assert called["confirm"] == 1
