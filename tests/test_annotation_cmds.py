"""Annotation Add / Edit / Delete commands round-trip via undo."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub():
    return types.SimpleNamespace(annotations=[])


def test_add_annotation_appends_and_undo_removes():
    app = _stub()
    ann = {"text": "Lobby", "color": "#FFD060", "font_size": 18, "x": 10, "y": 20}
    cmd = cwt.AddAnnotationCmd(app, ann)
    cmd.do()
    assert app.annotations == [ann]
    cmd.undo()
    assert app.annotations == []


def test_delete_annotation_restores_at_original_index():
    app = _stub()
    a = {"text": "A", "x": 0, "y": 0}
    b = {"text": "B", "x": 1, "y": 1}
    c = {"text": "C", "x": 2, "y": 2}
    app.annotations = [a, b, c]
    cmd = cwt.DeleteAnnotationCmd(app, b, index=1)
    cmd.do()
    assert app.annotations == [a, c]
    cmd.undo()
    assert app.annotations == [a, b, c]


def test_edit_annotation_in_place_round_trip():
    app = _stub()
    ann = {"text": "old", "color": "#FFFFFF", "font_size": 12, "x": 0, "y": 0}
    app.annotations = [ann]
    old = {"text": "old", "color": "#FFFFFF", "font_size": 12}
    new = {"text": "new", "color": "#FF0000", "font_size": 24}
    cmd = cwt.EditAnnotationCmd(app, ann, old, new)
    cmd.do()
    assert ann["text"] == "new"
    assert ann["color"] == "#FF0000"
    assert ann["font_size"] == 24
    # x/y untouched
    assert ann["x"] == 0 and ann["y"] == 0
    cmd.undo()
    assert ann["text"] == "old"
    assert ann["color"] == "#FFFFFF"
    assert ann["font_size"] == 12
    # Same dict identity preserved (so callers holding refs still work)
    assert app.annotations[0] is ann
