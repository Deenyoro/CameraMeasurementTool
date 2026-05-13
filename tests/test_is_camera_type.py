"""Tests for CameraWireTool._is_camera_type — the symbol-type camera
flag accessor. The whole point of the helper is to absorb corrupted
non-dict entries without AttributeError; lock that contract in."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camera_wire_tool import CameraWireTool


def _stub(symbol_types):
    """Minimal CameraWireTool stand-in carrying just symbol_types so we
    can call the unbound method without spinning up Tk."""
    return types.SimpleNamespace(symbol_types=symbol_types)


def test_returns_true_when_entry_has_is_camera_true():
    app = _stub({"MD": {"is_camera": True}})
    assert CameraWireTool._is_camera_type(app, "MD") is True


def test_returns_false_when_entry_has_is_camera_false():
    app = _stub({"CR": {"is_camera": False}})
    assert CameraWireTool._is_camera_type(app, "CR") is False


def test_returns_false_when_entry_missing_is_camera():
    """Treat a symbol entry without is_camera as non-camera (legacy
    catalog entries don't always have the flag)."""
    app = _stub({"X": {"label": "Thing"}})
    assert CameraWireTool._is_camera_type(app, "X") is False


def test_returns_false_when_type_code_not_in_catalog():
    app = _stub({})
    assert CameraWireTool._is_camera_type(app, "MD") is False


def test_returns_false_for_corrupted_non_dict_entry():
    """The whole reason the helper exists — a hand-edited project file
    could carry 'MD': 'stringified' and the old chained .get().get()
    would AttributeError. Now: just False."""
    app = _stub({"MD": "not a dict"})
    assert CameraWireTool._is_camera_type(app, "MD") is False


def test_returns_false_for_none_type_code():
    """Some call paths pass None when a wire has no device_id."""
    app = _stub({"MD": {"is_camera": True}})
    assert CameraWireTool._is_camera_type(app, None) is False


def test_returns_bool_not_truthy_string():
    """Some symbol entries might carry is_camera as a truthy string
    instead of a real bool. Helper should coerce to bool so callers
    can rely on the return type."""
    app = _stub({"MD": {"is_camera": "yes"}})
    result = CameraWireTool._is_camera_type(app, "MD")
    assert result is True
    assert isinstance(result, bool)
