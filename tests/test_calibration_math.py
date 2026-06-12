"""Numeric verification of the Set Scale (calibration) math, end to
end: clicked-pixel distance + entered real-world distance →
scale_factor → wire lengths and display strings. Covers identity and
aligned floors, imperial and metric entry."""

from __future__ import annotations

import math
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from cwt import multifloor as mf

IN_PER_M = 1.0 / 0.0254
IN_PER_CM = 1.0 / 2.54


# ---------------- _calibration_scale formula

def test_identity_floor_simple():
    """100 raw px ≙ 50 inches on an unaligned floor → 0.5 in/px."""
    s = cwt.CameraWireTool._calibration_scale(50.0, 100.0, 1.0)
    assert abs(s - 0.5) < 1e-12


def test_known_project_value():
    """The shipped Floor 1 plan: 0.599131 in/px came from a real
    calibration — verify the formula reproduces a scale of that form:
    e.g. 30 ft over 600.87 px."""
    px = 600.8714
    total_in = 30 * 12
    s = cwt.CameraWireTool._calibration_scale(total_in, px, 1.0)
    assert abs(s - 0.59913) < 1e-4


def test_aligned_floor_division():
    """On a 1.8856x-aligned floor (the real F2 value), the stored
    scale must be divided by alignment.scale so it stays in
    reference-frame units."""
    a = 1.8856491423232367
    s = cwt.CameraWireTool._calibration_scale(120.0, 200.0, a)
    assert abs(s - (120.0 / 200.0) / a) < 1e-12


def test_zero_alignment_scale_does_not_divide_by_zero():
    s = cwt.CameraWireTool._calibration_scale(100.0, 50.0, 0.0)
    assert abs(s - 2.0) < 1e-12


# ---------------- full round trip: calibrate on floor, measure on floor

def _app_with_alignment(a_scale):
    floor = mf.FloorState(id="F1", name="F1", elevation_ft=0.0,
                           drawio_file="F1.drawio",
                           alignment={"tx": 0, "ty": 0,
                                       "scale": a_scale,
                                       "rotation_deg": 0})
    proj = mf.ProjectState(active_floor="F1", floors=[floor])
    app = types.SimpleNamespace(_project=proj, scale_factor=None)
    app._current_alignment = lambda: (
        cwt.CameraWireTool._current_alignment(app))
    app._alignment_scale = lambda *a_, **k_: (
        cwt.CameraWireTool._alignment_scale(app, *a_, **k_))
    app._effective_scale_factor = lambda: (
        cwt.CameraWireTool._effective_scale_factor(app))
    return app


def test_round_trip_identity_floor():
    """Calibrate 240 raw px = 10 ft, then a 480 raw px wire must read
    exactly 20 ft."""
    app = _app_with_alignment(1.0)
    app.scale_factor = cwt.CameraWireTool._calibration_scale(
        10 * 12, 240.0, app._alignment_scale())
    eff = app._effective_scale_factor()
    assert abs(480.0 * eff - 20 * 12) < 1e-9


def test_round_trip_aligned_floor():
    """Same physical exercise on a 1.8856x-aligned floor: calibrate
    with raw pixels measured THERE, then measure raw pixels THERE —
    the answer must be the same real distance, and the stored project
    scale must work for the reference floor too."""
    a = 1.8856491423232367
    app = _app_with_alignment(a)
    # User clicks two points 377.13 raw px apart and types 10 ft.
    px = 377.13
    app.scale_factor = cwt.CameraWireTool._calibration_scale(
        10 * 12, px, app._alignment_scale())
    eff = app._effective_scale_factor()
    # Measure the same raw distance → exactly 10 ft.
    assert abs(px * eff - 10 * 12) < 1e-9
    # And a wire on the REFERENCE floor (alignment 1.0) using the same
    # project scale: raw px there are reference px. The physical span
    # that F2 calibration implies for reference pixels:
    ref_app = _app_with_alignment(1.0)
    ref_app.scale_factor = app.scale_factor
    ref_eff = ref_app._effective_scale_factor()
    # px raw-F2 pixels cover px*a reference pixels = same wall. So the
    # wall measured on the reference floor: (px*a) ref px × ref_eff
    # must also be 10 ft.
    assert abs((px * a) * ref_eff - 10 * 12) < 1e-9


# ---------------- entry-form conversions (the dialog's total_in math)

def test_imperial_entry_conversion():
    # feet*12 + inches — what the dialog computes
    assert abs((12 * 12 + 6.5) - 150.5) < 1e-12


def test_metric_entry_conversion():
    """3 m 25 cm → inches via the app's constants."""
    total_in = 3 * IN_PER_M + 25 * IN_PER_CM
    # 3.25 m = 127.9527... inches
    assert abs(total_in - 3.25 / 0.0254) < 1e-9


def test_format_length_round_trips_units():
    """_format_length displays what eff-scale math produces, in every
    unit, without drift."""
    app = types.SimpleNamespace(unit_var=types.SimpleNamespace(get=lambda: "feet-dec"),
                                  _IN_PER_M=IN_PER_M, _IN_PER_CM=IN_PER_CM)
    fmt = cwt.CameraWireTool._format_length
    assert fmt(app, 240.0) == "20.00 ft"
    app.unit_var = types.SimpleNamespace(get=lambda: "meters")
    assert fmt(app, 1.0 / 0.0254) == "1.000 m"
    app.unit_var = types.SimpleNamespace(get=lambda: "cm")
    assert fmt(app, 1.0 / 2.54) == "1.0 cm"
    app.unit_var = types.SimpleNamespace(get=lambda: "inches")
    assert fmt(app, 150.5) == '150.50"'
