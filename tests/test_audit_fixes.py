"""Tests for the UI/UX audit fix batch: drop-point undo commands,
legend-move undo, and the category show-on-map preset."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf


def _drop_app():
    proj = mf.ProjectState(
        active_floor="F1",
        floors=[mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                               drawio_file="F1.drawio")])
    app = types.SimpleNamespace(_project=proj, _selected_drop=None)
    return app


def test_add_drop_do_undo():
    app = _drop_app()
    drop = {"id": "DP1", "label": "DP1", "x": 10.0, "y": 20.0,
            "floors": ["F1"], "home_floor": "F1"}
    cmd = cwt.AddDropCmd(app, drop)
    cmd.do()
    assert drop in app._project.drop_points
    cmd.undo()
    assert drop not in app._project.drop_points


def test_add_drop_do_idempotent():
    app = _drop_app()
    drop = {"id": "DP1", "x": 0, "y": 0, "floors": ["F1"]}
    cmd = cwt.AddDropCmd(app, drop)
    cmd.do(); cmd.do()
    assert app._project.drop_points.count(drop) == 1


def test_add_drop_undo_clears_selection():
    app = _drop_app()
    drop = {"id": "DP1", "x": 0, "y": 0, "floors": ["F1"]}
    cmd = cwt.AddDropCmd(app, drop)
    cmd.do()
    app._selected_drop = drop
    cmd.undo()
    assert app._selected_drop is None


def test_delete_drop_restores_at_original_index():
    app = _drop_app()
    d1 = {"id": "DP1", "x": 0, "y": 0, "floors": ["F1"]}
    d2 = {"id": "DP2", "x": 1, "y": 1, "floors": ["F1"]}
    d3 = {"id": "DP3", "x": 2, "y": 2, "floors": ["F1"]}
    app._project.drop_points.extend([d1, d2, d3])
    cmd = cwt.DeleteDropCmd(app, d2)
    cmd.do()
    assert app._project.drop_points == [d1, d3]
    cmd.undo()
    assert app._project.drop_points == [d1, d2, d3]


def test_delete_drop_undo_idempotent():
    app = _drop_app()
    d1 = {"id": "DP1", "x": 0, "y": 0, "floors": ["F1"]}
    app._project.drop_points.append(d1)
    cmd = cwt.DeleteDropCmd(app, d1)
    cmd.do()
    cmd.undo(); cmd.undo()
    assert app._project.drop_points.count(d1) == 1


def test_move_legend_round_trip():
    app = types.SimpleNamespace(
        legend_box={"x": 100.0, "y": 200.0, "w": 340, "title": "Symbols"})
    cmd = cwt.MoveLegendCmd(app, (100.0, 200.0), (400.0, 500.0))
    cmd.do()
    assert (app.legend_box["x"], app.legend_box["y"]) == (400.0, 500.0)
    cmd.undo()
    assert (app.legend_box["x"], app.legend_box["y"]) == (100.0, 200.0)


def test_move_legend_tolerates_corrupted_box():
    app = types.SimpleNamespace(legend_box=None)
    cmd = cwt.MoveLegendCmd(app, (0, 0), (10, 10))
    cmd.do()    # must not raise
    cmd.undo()  # must not raise


# ---------------- _show_category_only

def test_show_category_only_hides_other_categories():
    app = types.SimpleNamespace(
        symbol_types={
            "MD": {"label": "Dome", "is_camera": True},
            "CR": {"label": "Reader", "is_camera": False,
                    "category": "Security"},
            "WAP": {"label": "AP", "is_camera": False, "category": "IT"},
        },
        _hidden_types=set(),
    )
    app._safe_sym = lambda c: cwt.CameraWireTool._safe_sym(app, c)
    app._category_of = lambda c: cwt.CameraWireTool._category_of(app, c)
    app._all_known_types = lambda: set(app.symbol_types)
    applied = {"n": 0}
    app._apply_type_visibility = lambda: applied.__setitem__("n", applied["n"] + 1)
    cwt.CameraWireTool._show_category_only(app, "Security")
    assert app._hidden_types == {"MD", "WAP"}
    assert applied["n"] == 1
    cwt.CameraWireTool._show_category_only(app, "IT")
    assert app._hidden_types == {"MD", "CR"}
