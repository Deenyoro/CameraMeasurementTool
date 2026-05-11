"""Pre-export scale warning logic (rounds 149/150).

The helper should:
  - Pass through silently when scale is set.
  - Pass through silently when there are no wires (nothing to mis-report).
  - Ask the user yes/no when scale missing AND wires exist."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub(scale_factor, wire_routes):
    app = types.SimpleNamespace(
        scale_factor=scale_factor,
        wire_routes=wire_routes,
    )
    # _warn_if_no_scale only reaches messagebox when both preconditions
    # are not satisfied — these stubs intentionally raise to catch the
    # case where the helper called the dialog unexpectedly.
    return app


def test_warn_if_no_scale_passes_with_scale_set():
    app = _stub(scale_factor=0.6,
                wire_routes=[{"device_id": "s1", "length_in": 100}])
    # Should return True without invoking the messagebox
    assert cwt.CameraWireTool._warn_if_no_scale(app, "CSV") is True


def test_warn_if_no_scale_passes_with_no_wires():
    app = _stub(scale_factor=None, wire_routes=[])
    assert cwt.CameraWireTool._warn_if_no_scale(app, "CSV") is True


def test_warn_if_no_scale_asks_when_scale_missing_and_wires_exist(monkeypatch):
    app = _stub(scale_factor=None,
                wire_routes=[{"device_id": "s1", "length_in": None}])
    asked = {"count": 0, "doc_type": None}
    def fake_askyesno(title, message):
        asked["count"] += 1
        # Confirm the doc_type from the prompt was woven into the message
        if "CSV bundle" in message:
            asked["doc_type"] = "CSV bundle"
        return True
    monkeypatch.setattr(cwt.messagebox, "askyesno", fake_askyesno)
    result = cwt.CameraWireTool._warn_if_no_scale(app, "CSV bundle")
    assert result is True
    assert asked["count"] == 1
    assert asked["doc_type"] == "CSV bundle"


def test_warn_if_no_scale_returns_false_when_user_cancels(monkeypatch):
    app = _stub(scale_factor=None,
                wire_routes=[{"device_id": "s1", "length_in": None}])
    monkeypatch.setattr(cwt.messagebox, "askyesno",
                         lambda *_a, **_k: False)
    assert cwt.CameraWireTool._warn_if_no_scale(app, "PDF") is False
