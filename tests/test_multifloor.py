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


def test_annotations_round_trip_per_floor(tmp_path):
    """Annotations are stored per-floor (annotations_by_floor) — verify
    they survive save+load on both floors."""
    proj = mf.ProjectState(
        floors=[
            mf.FloorState(id="F1", name="First", elevation_ft=0,
                          drawio_file="F1.drawio",
                          annotations=[
                              {"text": "Lobby", "x": 100, "y": 200,
                               "color": "#FFD060", "font_size": 18},
                              {"text": "IDF", "x": 300, "y": 400,
                               "color": "#FF8C42", "font_size": 14},
                          ]),
            mf.FloorState(id="F2", name="Second", elevation_ft=14,
                          drawio_file="F2.drawio",
                          annotations=[
                              {"text": "Server Room", "x": 500, "y": 600,
                               "color": "#3ddc84", "font_size": 22},
                          ]),
        ],
        active_floor="F1",
    )
    p = tmp_path / "proj.wiremap.json"
    mf.save_project(proj, str(p))
    loaded = mf.load_project(str(p), str(tmp_path / "F1.drawio"))
    f1 = loaded.get_floor("F1")
    f2 = loaded.get_floor("F2")
    assert f1 is not None and f2 is not None
    assert len(f1.annotations) == 2
    assert len(f2.annotations) == 1
    assert f1.annotations[0]["text"] == "Lobby"
    assert f2.annotations[0]["text"] == "Server Room"
    # Per-annotation fields preserved
    assert f2.annotations[0]["font_size"] == 22
    assert f1.annotations[1]["color"] == "#FF8C42"


def test_v1_to_v2_migration_preserves_cable_types_and_annotations(tmp_path):
    """v1 files with custom cable_types or top-level annotations must
    have those carried into the migrated v2 project (audit-fix round 75)."""
    import json
    p = tmp_path / "old.wiremap.json"
    p.write_text(json.dumps({
        # No 'version' key → triggers v1 migration path
        "wire_routes": [],
        "scale_factor": 0.6,
        "cable_types": {
            "Custom": {"label": "Custom", "color": "#ABCDEF",
                        "cost_per_ft": 9.99},
        },
        "annotations": [{"text": "v1 era", "x": 50, "y": 50}],
    }))
    proj = mf.load_project(str(p), str(tmp_path / "F1.drawio"))
    assert "Custom" in proj.cable_types
    assert proj.cable_types["Custom"]["cost_per_ft"] == 9.99
    assert proj.floors[0].annotations == [{"text": "v1 era", "x": 50, "y": 50}]


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
