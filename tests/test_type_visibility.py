"""Tests for the per-type on-map visibility presets (show/hide device
types). Covers the pure set computation behind the Devices-pane controls
and the toolbar 'Cameras only' toggle, without touching Tk."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt

CWT = cwt.CameraWireTool


def _stub(symbol_types, *, devices=(), cameras=(), other=None):
    app = types.SimpleNamespace()
    app.symbol_types = symbol_types
    app.devices = list(devices)
    app._other_floor_devs = other or {}
    app._hidden_types = set()
    cam_set = set(cameras)
    app._is_camera_type = lambda t: t in cam_set
    # Bind the real helper so presets compute against it; stub the Tk side.
    app._all_known_types = lambda: CWT._all_known_types(app)
    app._apply_type_visibility = lambda: None
    return app


def test_all_known_types_unions_catalog_devices_and_other_floors():
    app = _stub(
        {"MD": {}, "CR": {}},
        devices=[{"type": "BC"}, {"type": "MD"}],
        other={"F2": [{"type": "PTZ"}, {"type": "CR"}]},
    )
    assert app._all_known_types() == {"MD", "CR", "BC", "PTZ"}


def test_all_known_types_ignores_corrupt_entries():
    app = _stub({"MD": {}}, devices=["junk", {"nope": 1}, {"type": 5}])
    assert app._all_known_types() == {"MD"}


def test_show_cameras_only_hides_non_cameras():
    app = _stub({"MD": {}, "360": {}, "CR": {}, "M": {}},
                cameras=("MD", "360"))
    CWT._show_cameras_only(app)
    assert app._hidden_types == {"CR", "M"}


def test_show_all_types_clears_hidden():
    app = _stub({"MD": {}, "CR": {}}, cameras=("MD",))
    app._hidden_types = {"CR"}
    CWT._show_all_types(app)
    assert app._hidden_types == set()


def test_hide_all_types_hides_everything_known():
    app = _stub({"MD": {}, "CR": {}}, devices=[{"type": "BC"}], cameras=("MD", "BC"))
    CWT._hide_all_types(app)
    assert app._hidden_types == {"MD", "CR", "BC"}


def test_show_only_type_hides_all_but_one():
    app = _stub({"MD": {}, "360": {}, "CR": {}}, cameras=("MD", "360"))
    CWT._show_only_type(app, "CR")
    assert app._hidden_types == {"MD", "360"}


def test_toggle_type_hidden_round_trips():
    app = _stub({"CR": {}})
    CWT._toggle_type_hidden(app, "CR")
    assert "CR" in app._hidden_types
    CWT._toggle_type_hidden(app, "CR")
    assert "CR" not in app._hidden_types


def test_sync_cameras_only_var_true_when_exactly_non_cameras_hidden():
    app = _stub({"MD": {}, "CR": {}}, cameras=("MD",))
    app._hidden_types = {"CR"}
    captured = {}
    app._cameras_only_var = types.SimpleNamespace(set=lambda v: captured.update(v=v))
    CWT._sync_cameras_only_var(app)
    assert captured["v"] is True


def test_sync_cameras_only_var_false_when_partial():
    app = _stub({"MD": {}, "CR": {}, "M": {}}, cameras=("MD",))
    app._hidden_types = {"CR"}  # M still visible → not "cameras only"
    captured = {}
    app._cameras_only_var = types.SimpleNamespace(set=lambda v: captured.update(v=v))
    CWT._sync_cameras_only_var(app)
    assert captured["v"] is False


def test_sync_cameras_only_var_false_when_no_cameras_exist():
    app = _stub({"CR": {}, "M": {}}, cameras=())
    app._hidden_types = {"CR", "M"}
    captured = {}
    app._cameras_only_var = types.SimpleNamespace(set=lambda v: captured.update(v=v))
    CWT._sync_cameras_only_var(app)
    assert captured["v"] is False
