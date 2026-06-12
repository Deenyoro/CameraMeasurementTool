"""Tests for the equipment-category system: symbol_meta persistence,
category defaults, the overlay/collect round-trip, and wire-follows-
device visibility used by the category-filtered PDF export."""

from __future__ import annotations

import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


# ---------------- coerce_symbol_meta

def test_coerce_keeps_valid_fields():
    meta = mf.coerce_symbol_meta({
        "category": " IT ", "cable_type": "Cat6", "is_run_point": 1})
    assert meta == {"category": "IT", "cable_type": "Cat6",
                    "is_run_point": True}


def test_coerce_drops_junk():
    assert mf.coerce_symbol_meta("garbage") is None
    assert mf.coerce_symbol_meta(None) is None
    assert mf.coerce_symbol_meta({}) is None
    assert mf.coerce_symbol_meta({"category": 42, "cable_type": ""}) is None


def test_coerce_partial_entry():
    assert mf.coerce_symbol_meta({"category": "Security"}) == {
        "category": "Security"}


# ---------------- default_category_for

def test_camera_defaults_to_cameras():
    assert mf.default_category_for({"is_camera": True}) == "Cameras"


def test_non_camera_defaults_to_security():
    assert mf.default_category_for({"is_camera": False}) == "Security"
    assert mf.default_category_for({}) == "Security"
    assert mf.default_category_for("junk") == "Security"


# ---------------- save/load round-trip

def test_symbol_meta_round_trips_through_project_file(tmp_path):
    proj = mf.ProjectState(
        floors=[mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                               drawio_file="F1.drawio")],
        symbol_meta={
            "WAP": {"category": "IT", "cable_type": "Cat6"},
            "MD": {"category": "Cameras"},
            "JUNK": "not a dict",          # dropped at save
        },
    )
    p = tmp_path / "cat.wiremap.json"
    mf.save_project(proj, str(p))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["symbol_meta"] == {
        "WAP": {"category": "IT", "cable_type": "Cat6"},
        "MD": {"category": "Cameras"},
    }
    # And load brings it back
    loaded = mf.load_project(str(p), "F1.drawio")
    assert loaded.symbol_meta["WAP"]["category"] == "IT"
    assert loaded.symbol_meta["MD"] == {"category": "Cameras"}
    assert "JUNK" not in loaded.symbol_meta


def test_load_tolerates_corrupted_symbol_meta(tmp_path):
    p = tmp_path / "bad.wiremap.json"
    p.write_text(json.dumps({
        "version": 2,
        "floors": [{"id": "F1", "drawio_file": "F1.drawio"}],
        "symbol_meta": "not a dict",
    }), encoding="utf-8")
    loaded = mf.load_project(str(p), "F1.drawio")
    assert loaded.symbol_meta == {}


# ---------------- app-level helpers (stub-based)

def _app(symbol_types, symbol_meta=None):
    app = types.SimpleNamespace(symbol_types=symbol_types)
    if symbol_meta is not None:
        floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                               drawio_file="F1.drawio")
        app._project = mf.ProjectState(active_floor="F1", floors=[floor],
                                        symbol_meta=symbol_meta)
    else:
        app._project = None
    app._safe_sym = lambda code: (
        cwt.CameraWireTool._safe_sym(app, code))
    app._category_of = lambda code: (
        cwt.CameraWireTool._category_of(app, code))
    return app


def test_apply_symbol_meta_overlays_onto_types():
    app = _app({"WAP": {"label": "AP", "shape": "ellipse",
                         "color": "#80D0FF", "is_camera": False}},
                symbol_meta={"WAP": {"category": "IT",
                                       "cable_type": "Cat6"}})
    cwt.CameraWireTool._apply_symbol_meta(app)
    assert app.symbol_types["WAP"]["category"] == "IT"
    assert app.symbol_types["WAP"]["cable_type"] == "Cat6"


def test_apply_symbol_meta_skips_unknown_codes():
    app = _app({"MD": {"label": "Dome", "is_camera": True}},
                symbol_meta={"GONE": {"category": "IT"}})
    cwt.CameraWireTool._apply_symbol_meta(app)
    assert "GONE" not in app.symbol_types


def test_collect_symbol_meta_captures_only_meaningful():
    app = _app({
        "WAP": {"label": "AP", "category": "IT", "cable_type": "Cat6"},
        "MD": {"label": "Dome", "is_camera": True},   # nothing durable set
        "IDF": {"label": "Closet", "is_run_point": True},
    })
    out = cwt.CameraWireTool._collect_symbol_meta(app)
    assert out["WAP"] == {"category": "IT", "cable_type": "Cat6"}
    assert "MD" not in out
    assert out["IDF"] == {"is_run_point": True}


def test_category_of_explicit_then_defaults():
    app = _app({
        "WAP": {"label": "AP", "category": "IT"},
        "MD": {"label": "Dome", "is_camera": True},
        "CR": {"label": "Reader", "is_camera": False},
    })
    assert cwt.CameraWireTool._category_of(app, "WAP") == "IT"
    assert cwt.CameraWireTool._category_of(app, "MD") == "Cameras"
    assert cwt.CameraWireTool._category_of(app, "CR") == "Security"
    # Unknown code → safe default
    assert cwt.CameraWireTool._category_of(app, "NOPE") == "Security"


def test_all_categories_union_of_defaults_and_used():
    app = _app({
        "X1": {"label": "x", "category": "Fire"},
        "MD": {"label": "Dome", "is_camera": True},
    })
    cats = cwt.CameraWireTool._all_categories(app)
    assert "IT" in cats and "Security" in cats and "Cameras" in cats
    assert "Fire" in cats
    assert cats == sorted(cats)


# ---------------- wires follow device visibility

def _wire_app(devices, hidden_types):
    app = types.SimpleNamespace(devices=devices)
    app._hidden_types = set(hidden_types)
    app._hidden_by_unwired_filter = lambda d: False
    app._devices_by_id = lambda: (
        cwt.CameraWireTool._devices_by_id(app))
    return app


def test_wire_hidden_when_device_type_hidden():
    devices = [{"id": "s1", "type": "MD"}]
    app = _wire_app(devices, hidden_types={"MD"})
    assert cwt.CameraWireTool._wire_hidden(
        app, {"device_id": "s1", "points": [(0, 0), (1, 1)]}) is True


def test_wire_visible_when_device_type_shown():
    devices = [{"id": "s1", "type": "MD"}]
    app = _wire_app(devices, hidden_types={"CR"})
    assert cwt.CameraWireTool._wire_hidden(
        app, {"device_id": "s1", "points": [(0, 0), (1, 1)]}) is False


def test_unassigned_wire_never_hidden():
    app = _wire_app([], hidden_types={"MD"})
    assert cwt.CameraWireTool._wire_hidden(
        app, {"device_id": None, "points": [(0, 0), (1, 1)]}) is False


def test_wire_with_missing_device_not_hidden():
    """Orphaned wire (device deleted) stays visible so the user can see
    and clean it up — hiding it would make it unfindable."""
    app = _wire_app([{"id": "other", "type": "MD"}], hidden_types={"MD"})
    assert cwt.CameraWireTool._wire_hidden(
        app, {"device_id": "s_gone", "points": [(0, 0), (1, 1)]}) is False
