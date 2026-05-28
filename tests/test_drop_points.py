"""Tests for cross-floor cable drop points: the data model, coercion,
vertical-distance helpers, and save/load round-trip in cwt.multifloor."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import multifloor as mf


def _project(tmp_path):
    f1 = mf.FloorState(id="F1", name="First", elevation_ft=0.0,
                       drawio_file="f1.drawio")
    f2 = mf.FloorState(id="F2", name="Second", elevation_ft=25.0,
                       drawio_file="f2.drawio")
    return mf.ProjectState(version=2, active_floor="F1", floors=[f1, f2])


# ---------------- vertical distance helpers

def test_elevation_gap_basic():
    p = _project(None)
    assert mf.elevation_gap_ft(p, "F1", "F2") == 25.0
    assert mf.elevation_gap_ft(p, "F2", "F1") == 25.0  # absolute
    assert mf.elevation_gap_ft(p, "F1", "F1") == 0.0


def test_vertical_inches_is_feet_times_12():
    p = _project(None)
    assert mf.vertical_inches_between(p, "F1", "F2") == 300.0
    assert mf.vertical_inches_between(p, "F2", "F2") == 0.0


def test_elevation_gap_unknown_floor_is_zero():
    p = _project(None)
    assert mf.elevation_gap_ft(p, "F1", "NOPE") == 0.0


# ---------------- coercion

def test_coerce_minimal_valid_drop():
    d = mf.coerce_drop_point({"id": "DP1", "x": 10, "y": 20, "floors": ["F1"]})
    assert d == {"id": "DP1", "label": "DP1", "x": 10.0, "y": 20.0,
                 "floors": ["F1"], "home_floor": "F1"}


def test_coerce_full_drop_with_home_and_cable():
    d = mf.coerce_drop_point({
        "id": "DP2", "label": "Riser A", "x": 1.5, "y": 2.5,
        "floors": ["F1", "F2"], "home_floor": "F2", "cable_type": "Cat6"})
    assert d["home_floor"] == "F2"
    assert d["floors"] == ["F1", "F2"]
    assert d["cable_type"] == "Cat6"
    assert d["label"] == "Riser A"


def test_coerce_home_floor_forced_into_floors():
    # home_floor not in floors → falls back to first floor.
    d = mf.coerce_drop_point({"id": "DP3", "x": 0, "y": 0,
                              "floors": ["F1"], "home_floor": "F9"})
    assert d["home_floor"] == "F1"


def test_coerce_dedups_floors_preserving_order():
    d = mf.coerce_drop_point({"id": "DP4", "x": 0, "y": 0,
                              "floors": ["F2", "F1", "F2", "F1"]})
    assert d["floors"] == ["F2", "F1"]


def test_coerce_rejects_malformed():
    assert mf.coerce_drop_point("nope") is None
    assert mf.coerce_drop_point({"x": 0, "y": 0, "floors": ["F1"]}) is None  # no id
    assert mf.coerce_drop_point({"id": "D", "x": "z", "y": 0,
                                 "floors": ["F1"]}) is None  # bad coord
    assert mf.coerce_drop_point({"id": "D", "x": 0, "y": 0,
                                 "floors": []}) is None  # no floors


def test_coerce_filters_invalid_floor_ids_when_set_given():
    d = mf.coerce_drop_point(
        {"id": "DP5", "x": 0, "y": 0, "floors": ["F1", "GHOST"]},
        valid_floor_ids={"F1", "F2"})
    assert d["floors"] == ["F1"]


# ---------------- save / load round-trip

def test_drop_points_round_trip(tmp_path):
    p = _project(tmp_path)
    p.drop_points = [
        {"id": "DP1", "label": "Riser A", "x": 100.0, "y": 200.0,
         "floors": ["F1", "F2"], "home_floor": "F2", "cable_type": "Cat6"},
    ]
    path = str(tmp_path / "proj.wiremap.json")
    mf.save_project(p, path)
    loaded = mf.load_project(path, "f1.drawio")
    assert len(loaded.drop_points) == 1
    d = loaded.drop_points[0]
    assert d["id"] == "DP1"
    assert d["x"] == 100.0 and d["y"] == 200.0
    assert d["floors"] == ["F1", "F2"]
    assert d["home_floor"] == "F2"
    assert d["cable_type"] == "Cat6"


def test_save_drops_malformed_entries(tmp_path):
    p = _project(tmp_path)
    p.drop_points = [
        {"id": "ok", "x": 1, "y": 2, "floors": ["F1"]},
        "garbage",
        {"x": 0, "y": 0, "floors": ["F1"]},  # no id
    ]
    path = str(tmp_path / "proj.wiremap.json")
    mf.save_project(p, path)
    loaded = mf.load_project(path, "f1.drawio")
    assert [d["id"] for d in loaded.drop_points] == ["ok"]


def test_empty_project_has_no_drops(tmp_path):
    p = _project(tmp_path)
    path = str(tmp_path / "proj.wiremap.json")
    mf.save_project(p, path)
    loaded = mf.load_project(path, "f1.drawio")
    assert loaded.drop_points == []
