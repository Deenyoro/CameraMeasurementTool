"""Tests for cwt.fov — DORI math, AOV, cone polygon."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import fov, lenses


def test_horizontal_aov_4mm_on_quarter_inch_2_8():
    # 4mm lens on 1/2.8" sensor (5.37 x 3.02mm), expected ~67° horizontal AOV
    sw, _ = lenses.SENSORS['1/2.8"']
    aov = fov.horizontal_aov(sw, 4.0)
    assert 64 < aov < 72


def test_horizontal_aov_zero_lens_returns_zero():
    assert fov.horizontal_aov(5.37, 0.0) == 0.0
    assert fov.horizontal_aov(0.0, 4.0) == 0.0


def test_dori_distance_higher_ppm_means_closer_target():
    sw, _ = lenses.SENSORS['1/2.8"']
    d_detection = fov.dori_distance_meters(1920, sw, 4.0, fov.DORI_PPM["D"])
    d_identification = fov.dori_distance_meters(1920, sw, 4.0, fov.DORI_PPM["I"])
    # Identification (250 ppm) requires the target to be closer
    assert d_identification < d_detection
    # Roughly: ratio ~ I/D = 25/250 = 1/10
    assert abs(d_detection / d_identification - 10) < 0.5


def test_compute_fov_basic():
    settings = {
        "lens_mm": 4.0,
        "sensor": '1/2.8"',
        "resolution": [1920, 1080],
        "mount_height_ft": 9.0,
        "azimuth_deg": 0.0,
    }
    cone = fov.compute_fov(settings)
    assert 64 < cone.aov_h_deg < 72
    # All 4 DORI rings must be returned
    assert set(cone.distances_in.keys()) == {"D", "O", "R", "I"}
    # Detection ≥ Observation ≥ Recognition ≥ Identification (in distance)
    assert cone.distances_in["D"] >= cone.distances_in["O"]
    assert cone.distances_in["O"] >= cone.distances_in["R"]
    assert cone.distances_in["R"] >= cone.distances_in["I"]


def test_compute_fov_tolerates_bad_input():
    """Legacy or hand-edited project files may carry strings or None
    where compute_fov expects numbers. Should not raise — fall back
    to defaults silently."""
    # Strings where floats are expected
    cone = fov.compute_fov({"lens_mm": "4mm",
                              "sensor": '1/2.8"',
                              "resolution": [1920, 1080],
                              "mount_height_ft": "",
                              "azimuth_deg": None})
    assert cone.aov_h_deg > 0     # used default lens 4mm
    assert cone.azimuth_deg == 0.0
    # Bad resolution
    cone2 = fov.compute_fov({"lens_mm": 4.0, "resolution": "garbage"})
    assert set(cone2.distances_in.keys()) == {"D", "O", "R", "I"}


def test_project_to_floor_zero_when_below_height():
    # If a target is "closer than the camera is tall", floor projection is 0.
    height_m = 9 * 0.3048  # 9 ft
    assert fov.project_to_floor(height_m * 0.5, 9.0) == 0.0


def test_project_to_floor_pythagorean():
    # 5m slant, 3m height → 4m floor distance
    assert abs(fov.project_to_floor(5.0, 3.0 / 0.3048) - 4.0) < 1e-6


def test_cone_polygon_no_zero_radius():
    cone = fov.compute_fov({"lens_mm": 4.0, "sensor": '1/2.8"',
                              "resolution": [1920, 1080],
                              "mount_height_ft": 9.0, "azimuth_deg": 0.0})
    poly = fov.cone_polygon(cone, center_x=100.0, center_y=200.0,
                              radius_in=100.0, inches_per_pixel=1.0)
    # First and last points are the apex (camera position)
    assert poly[0] == (100.0, 200.0)
    assert poly[-1] == (100.0, 200.0)
    # Should have apex + n_segments+1 + apex points
    assert len(poly) >= 6


def test_cone_polygon_azimuth_rotates():
    cone = fov.compute_fov({"lens_mm": 4.0, "sensor": '1/2.8"',
                              "resolution": [1920, 1080],
                              "mount_height_ft": 9.0, "azimuth_deg": 0.0})
    poly_up = fov.cone_polygon(cone, center_x=0.0, center_y=0.0,
                                  radius_in=100.0, inches_per_pixel=1.0,
                                  azimuth_deg=0.0, n_segments=4)
    poly_right = fov.cone_polygon(cone, center_x=0.0, center_y=0.0,
                                     radius_in=100.0, inches_per_pixel=1.0,
                                     azimuth_deg=90.0, n_segments=4)
    # at 0° the cone center is straight "up" (negative y)
    # at 90° azimuth it's pointing right (positive x)
    mid = len(poly_up) // 2
    assert poly_up[mid][1] < 0
    assert poly_right[mid][0] > 0
