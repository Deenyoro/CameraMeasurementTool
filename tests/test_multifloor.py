"""Tests for cwt.multifloor — alignment math + project file v1→v2 migration."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import multifloor as mf


def test_identity_when_anchors_match():
    a = [(0.0, 0.0), (100.0, 0.0)]
    b = [(0.0, 0.0), (100.0, 0.0)]
    al = mf.compute_alignment(a, b)
    assert abs(al["tx"]) < 1e-6
    assert abs(al["ty"]) < 1e-6
    assert abs(al["scale"] - 1.0) < 1e-6
    assert abs(al["rotation_deg"]) < 1e-6


def test_pure_translation():
    a = [(50.0, 50.0), (150.0, 50.0)]
    b = [(0.0, 0.0), (100.0, 0.0)]
    al = mf.compute_alignment(a, b)
    assert abs(al["tx"] - 50.0) < 1e-6
    assert abs(al["ty"] - 50.0) < 1e-6
    assert abs(al["scale"] - 1.0) < 1e-6
    assert abs(al["rotation_deg"]) < 1e-6


def test_pure_scale():
    # B is half the size of A
    a = [(0.0, 0.0), (200.0, 0.0)]
    b = [(0.0, 0.0), (100.0, 0.0)]
    al = mf.compute_alignment(a, b)
    assert abs(al["scale"] - 2.0) < 1e-6


def test_pure_rotation_90():
    # rotate 90° clockwise
    a = [(0.0, 0.0), (0.0, 100.0)]   # b's "right" vector becomes a's "down"
    b = [(0.0, 0.0), (100.0, 0.0)]
    al = mf.compute_alignment(a, b)
    # In our convention (atan2(y, x)), a's vector points "down" (+y),
    # which is +90° from b's vector (+x).
    assert abs(al["rotation_deg"] - 90.0) < 1e-3


def test_apply_alignment_round_trip():
    a = [(50.0, 50.0), (250.0, 150.0)]
    b = [(0.0, 0.0), (100.0, 0.0)]
    al = mf.compute_alignment(a, b)
    # Applying the transform to b1 should yield a1
    out0 = mf.apply_alignment(b[0], al)
    out1 = mf.apply_alignment(b[1], al)
    assert abs(out0[0] - a[0][0]) < 1e-6
    assert abs(out0[1] - a[0][1]) < 1e-6
    assert abs(out1[0] - a[1][0]) < 1e-6
    assert abs(out1[1] - a[1][1]) < 1e-6


def test_inverse_alignment():
    al = {"tx": 50.0, "ty": 30.0, "scale": 2.0, "rotation_deg": 30.0}
    inv = mf.inverse_alignment(al)
    pt = (10.0, 20.0)
    forward = mf.apply_alignment(pt, al)
    back = mf.apply_alignment(forward, inv)
    assert abs(back[0] - pt[0]) < 1e-6
    assert abs(back[1] - pt[1]) < 1e-6


def test_load_project_v1_migration(tmp_path):
    # Write a v1-style file (no "version" key)
    p = tmp_path / "Floor1.wiremap.json"
    p.write_text(json.dumps({
        "drawio_file": "Floor1.drawio",
        "scale_factor": 0.5991,
        "unit": "ft-in",
        "next_device_id": 100,
        "wire_routes": [{"points": [[10, 20], [30, 40]], "device_id": "s10"}],
    }))
    proj = mf.load_project(str(p), str(tmp_path / "Floor1.drawio"))
    assert proj.version == 2
    assert len(proj.floors) == 1
    assert proj.floors[0].id == "F1"
    assert proj.floors[0].drawio_file == "Floor1.drawio"
    assert len(proj.floors[0].wire_routes) == 1
    assert proj.scale_factor == 0.5991


def test_load_project_v2_round_trip(tmp_path):
    proj = mf.ProjectState(
        floors=[
            mf.FloorState(id="F1", name="First", elevation_ft=0,
                          drawio_file="F1.drawio",
                          wire_routes=[{"points": [[1, 2], [3, 4]],
                                        "device_id": "s1"}]),
            mf.FloorState(id="F2", name="Second", elevation_ft=14,
                          drawio_file="F2.drawio"),
        ],
        active_floor="F2",
        cable_types={"Cat6": {"label": "Cat6", "color": "#0080FF",
                              "cost_per_ft": 0.18}},
        scale_factor=0.6,
    )
    p = tmp_path / "proj.wiremap.json"
    mf.save_project(proj, str(p))
    loaded = mf.load_project(str(p), str(tmp_path / "F1.drawio"))
    assert loaded.version == 2
    assert len(loaded.floors) == 2
    assert loaded.active_floor == "F2"
    assert loaded.scale_factor == 0.6
    assert "Cat6" in loaded.cable_types
    assert len(loaded.floors[0].wire_routes) == 1


def test_load_project_missing_returns_empty(tmp_path):
    proj = mf.load_project(str(tmp_path / "nope.json"),
                            str(tmp_path / "F1.drawio"))
    assert len(proj.floors) == 1
    assert proj.floors[0].drawio_file == "F1.drawio"


def test_load_project_skips_malformed_floor_entries(tmp_path):
    """A floor entry missing 'id' or 'drawio_file' shouldn't crash the
    loader — just skip the bad entry and keep the good ones."""
    import json
    p = tmp_path / "broken.wiremap.json"
    p.write_text(json.dumps({
        "version": 2,
        "active_floor": "F1",
        "floors": [
            {"id": "F1", "name": "Good", "elevation_ft": 0,
             "drawio_file": "F1.drawio"},
            {"id": "F2"},                       # missing drawio_file
            {"drawio_file": "F3.drawio"},       # missing id
            "garbage",                          # not even a dict
            {"id": "F4", "drawio_file": "F4.drawio",
             "elevation_ft": "not a number"},   # bad elevation
        ],
    }))
    proj = mf.load_project(str(p), str(tmp_path / "F1.drawio"))
    assert {f.id for f in proj.floors} == {"F1", "F4"}
    f4 = next(f for f in proj.floors if f.id == "F4")
    assert f4.elevation_ft == 0.0


def test_load_project_handles_non_numeric_next_device_id(tmp_path):
    import json
    p = tmp_path / "weird.wiremap.json"
    p.write_text(json.dumps({
        "version": 2,
        "active_floor": "F1",
        "floors": [{"id": "F1", "name": "G", "elevation_ft": 0,
                    "drawio_file": "F1.drawio"}],
        "next_device_id": "abc",   # not an int
        "scale_factor": "garbage", # not a float
    }))
    proj = mf.load_project(str(p), str(tmp_path / "F1.drawio"))
    assert proj.next_device_id == 100
    assert proj.scale_factor is None
