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


def test_load_project_normalizes_partial_alignment(tmp_path):
    """A hand-edited alignment can be a partial dict (missing tx or scale)
    or carry non-numeric values for some keys. The loader should fill the
    missing keys from IDENTITY so apply_alignment / inverse_alignment
    never KeyError on dict[k] indexing downstream."""
    import json
    p = tmp_path / "partial.wiremap.json"
    p.write_text(json.dumps({
        "version": 2,
        "active_floor": "F1",
        "floors": [
            {"id": "F1", "name": "G", "elevation_ft": 0,
             "drawio_file": "F1.drawio",
             # partial alignment: scale missing, rotation_deg is a string
             "alignment": {"tx": 5.0, "ty": -3.5, "rotation_deg": "broken"}},
        ],
    }))
    proj = mf.load_project(str(p), str(tmp_path / "F1.drawio"))
    a = proj.floors[0].alignment
    # All four keys present + the valid user values preserved
    assert set(a.keys()) == {"tx", "ty", "scale", "rotation_deg"}
    assert a["tx"] == 5.0
    assert a["ty"] == -3.5
    assert a["scale"] == 1.0           # IDENTITY default (missing key)
    assert a["rotation_deg"] == 0.0    # IDENTITY default (non-numeric)
    # apply_alignment should now work without raising on the loaded value
    x, y = mf.apply_alignment((10.0, 20.0), a)
    assert x == 15.0 and y == 16.5


def test_load_project_coerces_wrong_typed_containers(tmp_path):
    """Container fields (floors, cable_types, risers, wire_routes_by_floor,
    annotations_by_floor, anchor_points) could be the wrong shape after a
    hand-edit. Loader should fall back to empty containers, not crash."""
    import json
    p = tmp_path / "bad_containers.wiremap.json"
    p.write_text(json.dumps({
        "version": 2,
        "active_floor": "F1",
        "floors": [
            {"id": "F1", "name": "G", "elevation_ft": 0,
             "drawio_file": "F1.drawio",
             "alignment": "not a dict",                # wrong type
             "anchor_points": [[1, 2], "not a pair", [3]],  # mix of bad
             }
        ],
        "wire_routes_by_floor": "should be a dict",   # wrong type
        "annotations_by_floor": ["also wrong"],
        "cable_types": "not a dict either",
        "risers": "definitely not a list",
    }))
    proj = mf.load_project(str(p), str(tmp_path / "F1.drawio"))
    assert len(proj.floors) == 1
    f1 = proj.floors[0]
    assert f1.alignment == mf.IDENTITY_ALIGNMENT
    assert f1.anchor_points == [[1.0, 2.0]]  # only the well-formed pair
    assert f1.wire_routes == []
    assert f1.annotations == []
    assert proj.cable_types == {}
    assert proj.risers == []


def test_load_project_coerces_wrong_typed_fields(tmp_path):
    """A hand-edited project file could carry the wrong type for fields
    that downstream consumers (StringVar, title bar) assume are strings.
    The loader should fall back to sensible defaults instead of letting
    the bad value reach the UI."""
    import json
    p = tmp_path / "weird.wiremap.json"
    p.write_text(json.dumps({
        "version": 2,
        "project_name": 1234,           # wrong type — int instead of str
        "unit": ["not", "a", "string"],  # wrong type — list
        "active_floor": None,            # wrong type — None
        "floors": [{"id": "F1", "name": "G", "elevation_ft": 0,
                    "drawio_file": "F1.drawio"}],
    }))
    proj = mf.load_project(str(p), str(tmp_path / "F1.drawio"))
    assert proj.project_name == ""        # coerced from 1234 → ""
    assert proj.unit == "ft-in"           # coerced from list → default
    assert proj.active_floor == "F1"      # fell back to first floor's id


def test_save_project_is_atomic_no_partial_file_on_failure(tmp_path, monkeypatch):
    """If os.replace raises after the tmp write, the destination must
    not exist and the .tmp staging file must be cleaned up. Locks in
    the tmp+rename + cleanup contract.
    """
    proj = mf.ProjectState(
        floors=[mf.FloorState(id="F1", name="First", elevation_ft=0.0,
                               drawio_file="F1.drawio")],
    )
    target = tmp_path / "p.wiremap.json"

    def boom(_src, _dst):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(mf.os, "replace", boom)

    raised = False
    try:
        mf.save_project(proj, str(target))
    except OSError:
        raised = True

    assert raised, "expected the simulated rename failure to surface"
    assert not target.exists(), "destination must not exist after failure"
    assert not (tmp_path / "p.wiremap.json.tmp").exists(), \
        "tmp staging file must be cleaned up"


def test_save_project_skips_malformed_wires_and_annotations(tmp_path):
    """save_project should not propagate corrupt in-memory state to disk —
    if a stray string sneaks into FloorState.wire_routes or .annotations
    (post-recovery, hand-mutated, etc.), the writer should filter it out
    rather than crash on list(wr['points']) or write garbage."""
    floor = mf.FloorState(
        id="F1", name="First", elevation_ft=0.0, drawio_file="F1.drawio",
        wire_routes=[
            "stray string",                                 # skipped
            {"points": "not a list", "device_id": "s1"},    # skipped
            {"points": [(0, 0)],                            # skipped — <2 pts
             "device_id": "s1"},
            {"points": [(0, 0), (1, 1)], "device_id": "s2",
             "cable_type": "Cat6"},                          # kept
        ],
        annotations=[
            "not a dict",                                    # skipped
            {"text": "Server Room", "x": 100, "y": 200},     # kept
        ],
    )
    proj = mf.ProjectState(floors=[floor])
    target = tmp_path / "p.wiremap.json"
    mf.save_project(proj, str(target))
    data = json.loads(target.read_text(encoding="utf-8"))
    wires = data["wire_routes_by_floor"]["F1"]
    assert len(wires) == 1
    assert wires[0]["device_id"] == "s2"
    assert wires[0]["cable_type"] == "Cat6"
    anns = data["annotations_by_floor"]["F1"]
    assert len(anns) == 1
    assert anns[0]["text"] == "Server Room"


def test_save_project_round_trip(tmp_path):
    """Atomic write actually produces a loadable v2 file at the
    destination (regression for the tmp+rename refactor)."""
    proj = mf.ProjectState(
        project_name="Test",
        floors=[mf.FloorState(id="F1", name="Ground", elevation_ft=0.0,
                               drawio_file="F1.drawio")],
        scale_factor=0.6,
        unit="ft-in",
        next_device_id=42,
    )
    target = tmp_path / "rt.wiremap.json"
    mf.save_project(proj, str(target))
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["project_name"] == "Test"
    assert data["scale_factor"] == 0.6
    assert data["next_device_id"] == 42
