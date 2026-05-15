"""Tests for CameraWireTool._find_owning_project_file — scans a
drawio's directory for any sibling .wiremap.json that lists this
drawio as a floor, so opening Floor 2 directly doesn't orphan the
project bundle."""

from __future__ import annotations

import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub(drawio_path, project_path):
    app = types.SimpleNamespace()
    app.drawio_path = drawio_path
    app.project_path = project_path
    return app


def test_returns_none_when_directory_has_no_other_projects(tmp_path):
    drawio = tmp_path / "Floor2.drawio"
    drawio.write_text("<mxfile/>", encoding="utf-8")
    project = tmp_path / "Floor2.wiremap.json"
    app = _stub(str(drawio), str(project))
    assert (cwt.CameraWireTool
            ._find_owning_project_file(app) is None)


def test_finds_sibling_project_listing_this_drawio(tmp_path):
    """Project bundle next to Floor 1 lists Floor 2 — opening Floor 2
    directly should adopt the bundle."""
    (tmp_path / "Floor1.drawio").write_text("<mxfile/>", encoding="utf-8")
    drawio = tmp_path / "Floor2.drawio"
    drawio.write_text("<mxfile/>", encoding="utf-8")
    project = tmp_path / "Floor1.wiremap.json"
    project.write_text(json.dumps({
        "version": 2,
        "floors": [
            {"id": "F1", "name": "First Floor",
             "drawio_file": "Floor1.drawio"},
            {"id": "F2", "name": "Second Floor",
             "drawio_file": "Floor2.drawio"},
        ],
    }), encoding="utf-8")
    app = _stub(str(drawio), str(tmp_path / "Floor2.wiremap.json"))
    found = cwt.CameraWireTool._find_owning_project_file(app)
    assert found is not None
    assert os.path.basename(found) == "Floor1.wiremap.json"


def test_skips_default_candidate(tmp_path):
    """If the default Floor2.wiremap.json exists and references Floor 2,
    we should NOT return it — it's already what _load_project tries by
    default. Looking for a different sibling."""
    drawio = tmp_path / "Floor2.drawio"
    drawio.write_text("<mxfile/>", encoding="utf-8")
    default = tmp_path / "Floor2.wiremap.json"
    default.write_text(json.dumps({
        "version": 2,
        "floors": [{"id": "F2", "drawio_file": "Floor2.drawio"}],
    }), encoding="utf-8")
    app = _stub(str(drawio), str(default))
    assert (cwt.CameraWireTool
            ._find_owning_project_file(app) is None)


def test_ignores_corrupted_project(tmp_path):
    """A sibling .wiremap.json that fails to parse must not crash; skip
    it and keep scanning."""
    drawio = tmp_path / "Floor2.drawio"
    drawio.write_text("<mxfile/>", encoding="utf-8")
    (tmp_path / "Garbage.wiremap.json").write_text("not json{",
                                                     encoding="utf-8")
    good = tmp_path / "Floor1.wiremap.json"
    good.write_text(json.dumps({
        "floors": [{"id": "F2", "drawio_file": "Floor2.drawio"}],
    }), encoding="utf-8")
    app = _stub(str(drawio), str(tmp_path / "Floor2.wiremap.json"))
    found = cwt.CameraWireTool._find_owning_project_file(app)
    assert found is not None
    assert os.path.basename(found) == "Floor1.wiremap.json"


def test_returns_none_when_no_sibling_references_us(tmp_path):
    """Sibling project exists but doesn't list our drawio — don't adopt it."""
    drawio = tmp_path / "Floor2.drawio"
    drawio.write_text("<mxfile/>", encoding="utf-8")
    other = tmp_path / "OtherProject.wiremap.json"
    other.write_text(json.dumps({
        "floors": [{"id": "F1", "drawio_file": "SomeoneElse.drawio"}],
    }), encoding="utf-8")
    app = _stub(str(drawio), str(tmp_path / "Floor2.wiremap.json"))
    assert (cwt.CameraWireTool
            ._find_owning_project_file(app) is None)


def test_tolerates_non_dict_floors_entry(tmp_path):
    """Hand-edited project file with a stray non-dict in the floors
    list shouldn't crash the scan."""
    drawio = tmp_path / "Floor2.drawio"
    drawio.write_text("<mxfile/>", encoding="utf-8")
    proj = tmp_path / "Bundle.wiremap.json"
    proj.write_text(json.dumps({
        "floors": [
            "garbage",
            None,
            {"id": "F2", "drawio_file": "Floor2.drawio"},
        ],
    }), encoding="utf-8")
    app = _stub(str(drawio), str(tmp_path / "Floor2.wiremap.json"))
    found = cwt.CameraWireTool._find_owning_project_file(app)
    assert found is not None
    assert os.path.basename(found) == "Bundle.wiremap.json"


def test_tolerates_missing_dir(tmp_path):
    """If the drawio's dir doesn't exist (e.g. transient path), don't
    crash — return None."""
    app = _stub("/nope/never/Floor2.drawio", "/nope/never/Floor2.wiremap.json")
    assert (cwt.CameraWireTool
            ._find_owning_project_file(app) is None)
