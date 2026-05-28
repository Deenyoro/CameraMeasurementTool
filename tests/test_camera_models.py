"""Tests for the camera-model catalog: coercion, effective-model resolution
(per-device override > per-type default), and save/load round-trip."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import multifloor as mf


def _project():
    f1 = mf.FloorState(id="F1", name="1", elevation_ft=0.0, drawio_file="f1.drawio")
    return mf.ProjectState(version=2, active_floor="F1", floors=[f1])


# ---------------- coercion

def test_coerce_minimal():
    m = mf.coerce_camera_model({"id": "M1"})
    assert m == {"id": "M1", "name": "M1", "model_number": "", "price": 0.0}


def test_coerce_full():
    m = mf.coerce_camera_model({
        "id": "M1", "name": "Axis P3245", "model_number": "P3245-LV",
        "price": "650", "fov": {"lens_mm": 2.8, "tilt_deg": 15}})
    assert m["name"] == "Axis P3245"
    assert m["model_number"] == "P3245-LV"
    assert m["price"] == 650.0
    assert m["fov"]["lens_mm"] == 2.8


def test_coerce_bad_price_is_zero():
    assert mf.coerce_camera_model({"id": "M1", "price": "junk"})["price"] == 0.0
    assert mf.coerce_camera_model({"id": "M1", "price": -5})["price"] == 0.0


def test_coerce_rejects_no_id():
    assert mf.coerce_camera_model({"name": "x"}) is None
    assert mf.coerce_camera_model("nope") is None


# ---------------- effective model resolution

def _proj_with_models():
    p = _project()
    p.camera_models = [
        mf.coerce_camera_model({"id": "MA", "name": "Model A", "price": 100}),
        mf.coerce_camera_model({"id": "MB", "name": "Model B", "price": 200}),
    ]
    p.type_default_models = {"MD": "MA"}
    p.device_models = {"s5": "MB"}
    return p


def test_effective_uses_type_default():
    p = _proj_with_models()
    assert mf.effective_model_id(p, "s1", "MD") == "MA"


def test_effective_device_override_wins():
    p = _proj_with_models()
    # s5 has an override to MB even though its type defaults to MA.
    assert mf.effective_model_id(p, "s5", "MD") == "MB"


def test_effective_none_when_no_mapping():
    p = _proj_with_models()
    assert mf.effective_model_id(p, "s9", "BC") is None


def test_effective_ignores_stale_id():
    p = _proj_with_models()
    p.device_models = {"s5": "GHOST"}      # points at a deleted model
    p.type_default_models = {"MD": "MA"}
    assert mf.effective_model_id(p, "s5", "MD") == "MA"   # falls back to default


def test_get_camera_model():
    p = _proj_with_models()
    assert mf.get_camera_model(p, "MB")["name"] == "Model B"
    assert mf.get_camera_model(p, "NOPE") is None
    assert mf.get_camera_model(p, None) is None


# ---------------- persistence

def test_models_round_trip(tmp_path):
    p = _proj_with_models()
    path = str(tmp_path / "proj.wiremap.json")
    mf.save_project(p, path)
    loaded = mf.load_project(path, "f1.drawio")
    assert [m["id"] for m in loaded.camera_models] == ["MA", "MB"]
    assert loaded.type_default_models == {"MD": "MA"}
    assert loaded.device_models == {"s5": "MB"}
    assert mf.get_camera_model(loaded, "MA")["price"] == 100.0


def test_empty_project_has_no_models(tmp_path):
    p = _project()
    path = str(tmp_path / "proj.wiremap.json")
    mf.save_project(p, path)
    loaded = mf.load_project(path, "f1.drawio")
    assert loaded.camera_models == []
    assert loaded.type_default_models == {}
    assert loaded.device_models == {}
